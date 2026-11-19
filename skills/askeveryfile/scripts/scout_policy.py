"""Bind a scan to its first requested agent, model, and effort."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scan_artifacts import read_json, write_json_exclusive_atomic
from scan_contract import scout_policy_errors


def load_scout_policy(scan_dir: Path) -> dict[str, str]:
    try:
        value = read_json(scan_dir / "scout-policy.json")
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read scan scout policy: {exc}") from exc
    errors = scout_policy_errors(value)
    if errors:
        raise ValueError("; ".join(errors))
    return value


def bind_scout_policy(scan_dir: Path, requested: dict[str, Any]) -> dict[str, str]:
    errors = scout_policy_errors(requested)
    if errors:
        raise ValueError("; ".join(errors))
    path = scan_dir / "scout-policy.json"
    try:
        write_json_exclusive_atomic(path, requested)
    except FileExistsError:
        pass
    recorded = load_scout_policy(scan_dir)
    if requested != recorded:
        raise ValueError("Requested scout agent/model/effort does not match scan policy")
    return recorded
