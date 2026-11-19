#!/usr/bin/env python3
"""Prepare a private Ask Every File workspace outside the target repository."""

from __future__ import annotations

import argparse
import json

from scan_workspace import WorkspaceError, prepare_workspace


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("target", help="Directory whose text will be analyzed")
    parser.add_argument("topic", help="Short kebab-case scan topic")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = prepare_workspace(args.target, args.topic)
    except (OSError, WorkspaceError) as error:
        raise SystemExit(str(error)) from error
    print(json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
