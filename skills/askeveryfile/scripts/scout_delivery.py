"""Build contained scout prompts and materialize their line evidence."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from string import Template
from typing import Any

from scan_artifacts import sha256_file
from scan_contract import MODEL_OUTPUT_SCHEMA

PROMPT_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent / "templates" / "scout-prompt.md"
)
SCOUT_PROMPT_TEMPLATE = Template(
    PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8").removesuffix("\n")
)


def read_unit_text(
    root: Path, unit: dict[str, Any], *, verify_hash: bool = False
) -> str:
    path = root / unit["path"]
    if verify_hash:
        actual = sha256_file(path)
        if actual != unit["file_sha256"]:
            raise RuntimeError(
                f"{unit['path']} changed after planning; replan before running: "
                f"expected {unit['file_sha256']}, got {actual}"
            )
    selected: list[bytes] = []
    with path.open("rb") as handle:
        for line_number, line in enumerate(handle, start=1):
            if line_number < unit["start_line"]:
                continue
            if unit["end_line"] is not None and line_number > unit["end_line"]:
                break
            selected.append(line)
    return b"".join(selected).decode("utf-8", errors="replace")


def numbered_content(content: str, start_line: int) -> str:
    lines = content.splitlines(keepends=True)
    if not lines and not content:
        return "<EMPTY UNIT>\n"
    return "".join(f"L{start_line + index}|{line}" for index, line in enumerate(lines))


def content_end_line(content: str, start_line: int) -> int | None:
    line_count = len(content.splitlines())
    return start_line + line_count - 1 if line_count else None


def extract_line_quote(
    evidence: dict[str, Any], delivered_units: dict[str, list[dict[str, Any]]]
) -> str:
    path = evidence.get("path")
    line = evidence.get("line")
    if not isinstance(path, str) or path not in delivered_units:
        raise ValueError(f"references undelivered path {path!r}")
    if type(line) is not int or line < 1:
        raise ValueError("has an invalid line")
    for record in delivered_units[path]:
        unit = record["unit"]
        effective_end = record.get("effective_end_line")
        if effective_end is None:
            effective_end = content_end_line(record["content"], unit["start_line"])
        if line < unit["start_line"] or (
            effective_end is not None and line > effective_end
        ):
            continue
        lines = record["content"].splitlines()
        line_index = line - unit["start_line"]
        if 0 <= line_index < len(lines):
            return lines[line_index]
    raise ValueError("line is outside the delivered unit")


def materialize_evidence(
    model_output: Any,
    delivered_units: dict[str, list[dict[str, Any]]],
) -> tuple[Any, list[str], int]:
    output = copy.deepcopy(model_output)
    errors: list[str] = []
    changes = 0
    if not isinstance(output, dict) or not isinstance(output.get("observations"), list):
        return output, ["model output has no observations array"], changes
    for observation_index, observation in enumerate(output["observations"]):
        if not isinstance(observation, dict) or not isinstance(
            observation.get("evidence"), list
        ):
            errors.append(f"observation[{observation_index}] has no evidence array")
            continue
        for evidence_index, evidence in enumerate(observation["evidence"]):
            label = f"observation[{observation_index}].evidence[{evidence_index}]"
            if not isinstance(evidence, dict):
                errors.append(f"{label} is invalid")
                continue
            try:
                quote = extract_line_quote(evidence, delivered_units)
            except ValueError as exc:
                errors.append(f"{label} {exc}")
                continue
            materialized = {
                "path": evidence["path"],
                "line": evidence["line"],
                "locator": f"L{evidence['line']}",
                "quote": quote,
            }
            if evidence != materialized:
                changes += 1
            observation["evidence"][evidence_index] = materialized
    return output, errors, changes


def output_schema_for_units(
    delivered_units: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    schema = copy.deepcopy(MODEL_OUTPUT_SCHEMA)
    evidence = schema["properties"]["observations"]["items"]["properties"]["evidence"]
    template = evidence["items"]
    bounded_items: list[dict[str, Any]] = []
    for path, records in sorted(delivered_units.items()):
        for record in records:
            end_line = record["effective_end_line"]
            if end_line is None:
                continue
            branch = copy.deepcopy(template)
            branch["properties"]["path"] = {"type": "string", "enum": [path]}
            branch["properties"]["line"]["minimum"] = record["unit"]["start_line"]
            branch["properties"]["line"]["maximum"] = end_line
            bounded_items.append(branch)
    if bounded_items:
        evidence["items"] = {"anyOf": bounded_items}
    else:
        schema["properties"]["observations"]["maxItems"] = 0
    return schema


def build_scout_prompt(
    shard: dict[str, Any],
) -> tuple[str, dict[str, list[dict[str, Any]]]]:
    root = Path(shard["root"])
    boundary = (
        f"ASKEVERYFILE_{shard['scan_id']}_{shard['shard_id']}_"
        f"{shard['question_sha256'][:12]}"
    )
    delivered_parts: list[str] = []
    delivered_units: dict[str, list[dict[str, Any]]] = {}
    citable_ranges: list[tuple[str, int, int]] = []
    for unit in shard["units"]:
        content = read_unit_text(root, unit, verify_hash=True)
        effective_end_line = content_end_line(content, unit["start_line"])
        metadata = {
            "unit_id": unit["unit_id"],
            "path": unit["path"],
            "start_line": unit["start_line"],
            "end_line": effective_end_line,
            "file_sha256": unit["file_sha256"],
        }
        delivered_parts.append(
            f"\n<<<{boundary}:UNIT {json.dumps(metadata, ensure_ascii=False)}>>>\n"
        )
        delivered_units.setdefault(unit["path"], []).append(
            {
                "unit": unit,
                "content": content,
                "effective_end_line": effective_end_line,
            }
        )
        if effective_end_line is not None:
            citable_ranges.append(
                (unit["path"], unit["start_line"], effective_end_line)
            )
        delivered_parts.append(numbered_content(content, unit["start_line"]))
        delivered_parts.append(f"\n<<<{boundary}:END_UNIT>>>\n")
    rendered = SCOUT_PROMPT_TEMPLATE.substitute(
        scan_id=shard["scan_id"],
        shard_id=shard["shard_id"],
        question_sha256=shard["question_sha256"],
        boundary=boundary,
        question=shard["question"],
        delivered_units="".join(delivered_parts),
        citable_ranges="".join(
            f"- {path}: L{start_line}-L{end_line}\n"
            for path, start_line, end_line in citable_ranges
        ),
    )
    return rendered, delivered_units
