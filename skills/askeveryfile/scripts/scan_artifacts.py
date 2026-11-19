"""Read, hash, and safely write artifacts shared by scan stages."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROTECTED_SCAN_FILE_NAMES = (
    "empty-mcp.json",
    "plan.json",
    "run.json",
    "scout-policy.json",
    "scan.md",
    "validation.json",
)
PROTECTED_SCAN_DIR_NAMES = ("logs", "prompts", "results", "schemas", "shards")


@dataclass(frozen=True)
class JsonArtifact:
    """Parsed JSON bound to the digest of its exact source bytes."""

    value: Any
    sha256: str


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json_artifact(path: Path) -> JsonArtifact:
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8"))
    return JsonArtifact(value=value, sha256=sha256_bytes(raw))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def render_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def write_json(path: Path, value: Any) -> None:
    path.write_text(render_json(value), encoding="utf-8")


def write_text_atomic(path: Path, value: str) -> None:
    """Replace one text artifact only after its complete bytes are on disk."""
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def write_json_atomic(path: Path, value: Any) -> None:
    write_text_atomic(path, render_json(value))


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def artifact_manifest_sha256(manifest: Mapping[str, str]) -> str:
    """Bind a named artifact set to exact bytes with stable serialization."""
    return sha256_text(
        json.dumps(
            dict(sorted(manifest.items())),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def path_is_within(path: Path, directory: Path) -> bool:
    return path == directory or directory in path.parents


def is_protected_scan_artifact(path: Path, scan_dir: Path) -> bool:
    protected_files = {scan_dir / name for name in PROTECTED_SCAN_FILE_NAMES}
    protected_dirs = [scan_dir / name for name in PROTECTED_SCAN_DIR_NAMES]
    return path in protected_files or any(
        path_is_within(path, directory) for directory in protected_dirs
    )


def snapshot_file_errors(plan: Mapping[str, Any], root: Path) -> list[str]:
    """Compare every planned source file with its snapshot digest."""
    errors: list[str] = []
    for file_info in plan["files"]:
        path = root / file_info["path"]
        try:
            actual = sha256_file(path)
        except OSError as exc:
            errors.append(f"{file_info['path']}: snapshot check failed: {exc}")
            continue
        if actual != file_info["sha256"]:
            errors.append(f"{file_info['path']}: changed since plan")
    return errors


def write_bytes_exclusive_atomic(path: Path, data: bytes) -> None:
    """Publish immutable bytes atomically, refusing any existing destination."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json_exclusive_atomic(path: Path, value: Any) -> None:
    write_bytes_exclusive_atomic(path, render_json(value).encode("utf-8"))
