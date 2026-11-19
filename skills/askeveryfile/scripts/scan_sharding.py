"""Estimate delivered text and bundle it into directory-local shards."""

from __future__ import annotations

import math
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

ESTIMATED_BYTES_PER_TOKEN = 3
HARD_FILE_BUDGET_MULTIPLIER = 2.25
MIN_STRUCTURAL_CHUNK_RATIO = 0.5


def estimated_numbered_tokens(path: Path) -> int:
    prompt_bytes = 0
    with path.open("rb") as handle:
        for line_number, line in enumerate(handle, start=1):
            prompt_bytes += len(line) + len(f"L{line_number}|".encode("ascii"))
    return max(1, math.ceil(prompt_bytes / ESTIMATED_BYTES_PER_TOKEN))


def whole_file_unit(file_info: dict[str, Any]) -> dict[str, Any]:
    return {
        "unit_id": f"{file_info['path']}#L1-",
        "path": file_info["path"],
        "start_line": 1,
        "end_line": None,
        "estimated_tokens": file_info["estimated_tokens"],
        "file_sha256": file_info["sha256"],
        "boundary": "whole_file",
    }


def structural_boundaries(path: Path, lines: list[bytes]) -> tuple[set[int], set[int]]:
    """Return primary and fallback zero-based starts of coherent sections."""
    primary: set[int] = set()
    fallback: set[int] = set()
    markdown = path.suffix.lower() in {".md", ".mdx", ".markdown"}
    json_document = path.suffix.lower() == ".json"
    for index in range(1, len(lines)):
        raw_current = lines[index]
        current = raw_current.lstrip()
        previous = lines[index - 1].strip()
        markdown_boundary = markdown and re.match(rb"#{1,6}\s", current)
        json_boundary = json_document and (
            re.match(rb'^  "[^"\\]+"\s*:\s*[\[{]', raw_current)
            or re.match(rb"^    \{\s*$", raw_current)
        )
        thematic_boundary = current in {b"---\n", b"---\r\n", b"---"}
        if markdown_boundary or json_boundary or thematic_boundary:
            primary.add(index)
        elif not previous:
            fallback.add(index)
    if not primary:
        primary, fallback = fallback, set()
    return primary, fallback


def split_structurally(
    root: Path,
    file_info: dict[str, Any],
    target_tokens: int,
    hard_file_tokens: int,
) -> list[dict[str, Any]]:
    if file_info["estimated_tokens"] <= hard_file_tokens:
        return [whole_file_unit(file_info)]

    path = root / file_info["path"]
    lines = path.read_bytes().splitlines(keepends=True)
    if not lines:
        return [whole_file_unit(file_info)]
    line_costs = [
        max(
            1,
            math.ceil(
                (len(line) + len(f"L{line_number}|".encode("ascii")))
                / ESTIMATED_BYTES_PER_TOKEN
            ),
        )
        for line_number, line in enumerate(lines, start=1)
    ]
    prefix = [0]
    for cost in line_costs:
        prefix.append(prefix[-1] + cost)
    primary_boundaries, fallback_boundaries = structural_boundaries(path, lines)
    minimum_tokens = max(1, math.floor(target_tokens * MIN_STRUCTURAL_CHUNK_RATIO))
    chunk_ceiling_tokens = max(target_tokens, math.floor(target_tokens * 1.25))

    units: list[dict[str, Any]] = []
    start = 0
    while start < len(lines):
        remaining = prefix[-1] - prefix[start]
        if remaining <= chunk_ceiling_tokens:
            end = len(lines)
            boundary = "structural_tail"
        else:
            end = 0
            boundary = ""
            for candidates, candidate_label in (
                (primary_boundaries, "structural"),
                (fallback_boundaries, "structural_fallback"),
            ):
                within_soft = [
                    candidate
                    for candidate in candidates
                    if candidate > start
                    and minimum_tokens
                    <= prefix[candidate] - prefix[start]
                    <= target_tokens
                ]
                if within_soft:
                    end = max(within_soft)
                    boundary = candidate_label
                    break
                after_soft = [
                    candidate
                    for candidate in candidates
                    if candidate > start
                    and target_tokens
                    < prefix[candidate] - prefix[start]
                    <= chunk_ceiling_tokens
                ]
                if after_soft:
                    end = min(after_soft)
                    boundary = f"{candidate_label}_oversize"
                    break
            if not end:
                end = start + 1
                while (
                    end < len(lines)
                    and prefix[end + 1] - prefix[start] <= target_tokens
                ):
                    end += 1
                boundary = "line_fallback"
        start_line = start + 1
        end_line = end
        units.append(
            {
                "unit_id": f"{file_info['path']}#L{start_line}-{end_line}",
                "path": file_info["path"],
                "start_line": start_line,
                "end_line": end_line,
                "estimated_tokens": max(1, prefix[end] - prefix[start]),
                "file_sha256": file_info["sha256"],
                "boundary": boundary,
            }
        )
        start = end
    return units


def pack_bundles(
    bundles: list[list[dict[str, Any]]], target_tokens: int
) -> list[list[dict[str, Any]]]:
    """Merge adjacent bundles into the largest ones that stay under the target."""
    packed: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_tokens = 0
    for bundle in bundles:
        bundle_tokens = sum(unit["estimated_tokens"] for unit in bundle)
        if current and current_tokens + bundle_tokens > target_tokens:
            packed.append(current)
            current = []
            current_tokens = 0
        current.extend(bundle)
        current_tokens += bundle_tokens
        if bundle_tokens > target_tokens:
            packed.append(current)
            current = []
            current_tokens = 0
    if current:
        packed.append(current)
    return packed


def directory_local_bundles(
    files: list[dict[str, Any]],
    units_by_path: dict[str, list[dict[str, Any]]],
    target_tokens: int,
) -> list[list[dict[str, Any]]]:
    """Bundle units by directory subtree so each shard stays topically coherent."""
    tree: dict[str, Any] = {"files": [], "children": {}}
    for file_info in files:
        node = tree
        parts = Path(file_info["path"]).parts
        for part in parts[:-1]:
            node = node["children"].setdefault(part, {"files": [], "children": {}})
        node["files"].append(file_info["path"])

    def all_units(node: dict[str, Any]) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        for path in sorted(node["files"]):
            collected.extend(units_by_path[path])
        for name in sorted(node["children"]):
            collected.extend(all_units(node["children"][name]))
        return collected

    def bundle_node(node: dict[str, Any]) -> list[list[dict[str, Any]]]:
        complete = all_units(node)
        if sum(unit["estimated_tokens"] for unit in complete) <= target_tokens:
            return [complete] if complete else []
        bundles: list[list[dict[str, Any]]] = []
        for path in sorted(node["files"]):
            file_units = units_by_path[path]
            bundles.extend(
                [[unit] for unit in file_units] if len(file_units) > 1 else [file_units]
            )
        for name in sorted(node["children"]):
            child = node["children"][name]
            child_units = all_units(child)
            if sum(unit["estimated_tokens"] for unit in child_units) <= target_tokens:
                bundles.append(child_units)
            else:
                bundles.extend(bundle_node(child))
        return pack_bundles(bundles, target_tokens)

    return bundle_node(tree)


def build_shards(
    root: Path,
    files: list[dict[str, Any]],
    target_tokens: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    hard_file_tokens = max(
        target_tokens,
        math.floor(target_tokens * HARD_FILE_BUDGET_MULTIPLIER),
    )
    units_by_path = {
        file_info["path"]: split_structurally(
            root,
            file_info,
            target_tokens,
            hard_file_tokens,
        )
        for file_info in files
    }
    bundles = directory_local_bundles(files, units_by_path, target_tokens)

    shards: list[dict[str, Any]] = []
    width = max(3, len(str(max(1, len(bundles)))))
    for index, bundle in enumerate(bundles, start=1):
        paths = [unit["path"] for unit in bundle]
        common = os.path.commonpath(paths) if paths else "."
        if common in paths:
            common = str(Path(common).parent)
        shards.append(
            {
                "shard_id": f"shard-{index:0{width}d}",
                "scope": common or ".",
                "estimated_tokens": sum(unit["estimated_tokens"] for unit in bundle),
                "unit_count": len(bundle),
                "units": bundle,
            }
        )
    split_paths = sorted(
        path for path, units in units_by_path.items() if len(units) > 1
    )
    return shards, {
        "hard_file_token_budget": hard_file_tokens,
        "split_files": split_paths,
        "split_file_count": len(split_paths),
        "unit_count": sum(len(units) for units in units_by_path.values()),
        "boundary_counts": dict(
            sorted(
                Counter(
                    unit["boundary"]
                    for units in units_by_path.values()
                    for unit in units
                ).items()
            )
        ),
    }
