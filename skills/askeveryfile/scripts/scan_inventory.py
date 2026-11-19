"""Select, classify, and snapshot text files for a scan plan."""

from __future__ import annotations

import fnmatch
import os
import stat
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scan_artifacts import sha256_file
from scan_sharding import estimated_numbered_tokens

DEFAULT_EXCLUDED_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".cache",
    ".mypy_cache",
    ".next",
    ".nuxt",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    ".letsworkitout",
    ".tmp",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "temp",
    "tmp",
    "vendor",
    "venv",
}
DEFAULT_EXCLUDED_FILE_NAMES = {
    ".DS_Store",
    "bun.lock",
    "bun.lockb",
    "Cargo.lock",
    "composer.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "poetry.lock",
    "uv.lock",
    "yarn.lock",
}
DEFAULT_EXCLUDED_GLOBS = ("*.map", "*.min.css", "*.min.js", "*.pyc")


def flatten_option_groups(groups: list[list[str]]) -> list[str]:
    return [value for group in groups for value in group]


def normalize_exclusion_specs(values: Iterable[str]) -> set[str]:
    normalized: set[str] = set()
    for value in values:
        candidate = value.replace(os.sep, "/").strip()
        while candidate.startswith("./"):
            candidate = candidate[2:]
        candidate = candidate.strip("/")
        if candidate:
            normalized.add(candidate)
    return normalized


def normalize_root_relative_specs(
    values: Iterable[str], root: Path, option: str
) -> set[str]:
    """Resolve explicit path arguments without allowing them to widen the target."""
    normalized: set[str] = set()
    for value in values:
        candidate = value.strip()
        if not candidate:
            continue
        expanded = Path(candidate).expanduser()
        resolved = (expanded if expanded.is_absolute() else root / expanded).resolve()
        if resolved != root and root not in resolved.parents:
            raise SystemExit(
                f"{option} must stay inside the target root {root}: {value}"
            )
        if not resolved.exists():
            raise SystemExit(f"{option} path does not exist inside {root}: {value}")
        normalized.add(resolved.relative_to(root).as_posix() or ".")
    return normalized


def path_relation(relative: str, specs: set[str]) -> str:
    """Classify a path as selected by, leading to, or outside explicit specs."""
    if any(
        spec == "." or relative == spec or relative.startswith(f"{spec}/")
        for spec in specs
    ):
        return "selected"
    if any(relative == "." or spec.startswith(f"{relative}/") for spec in specs):
        return "ancestor"
    return "outside"


@dataclass(frozen=True)
class ScopePolicy:
    """Own the set of paths that belong to the scan before exclusions apply."""

    only_specs: set[str]

    def relation(self, relative: str) -> str:
        return (
            "selected"
            if not self.only_specs
            else path_relation(relative, self.only_specs)
        )


@dataclass(frozen=True)
class ForceIncludePolicy:
    """Own the paths that override exclusions without changing scan scope."""

    specs: set[str]

    def relation(self, relative: str) -> str:
        return path_relation(relative, self.specs)


def validate_policy_overlap(only_specs: set[str], force_specs: set[str]) -> None:
    """Reject force-includes that cannot affect the explicitly limited scope."""
    if not only_specs:
        return
    for forced in sorted(force_specs):
        if not any(path_relation(forced, {only}) != "outside" for only in only_specs):
            raise SystemExit(f"--force-include path is outside --only scope: {forced}")


def matched_path_spec(name: str, relative: str, specs: set[str]) -> str | None:
    for spec in sorted(specs):
        if ("/" in spec and relative == spec) or ("/" not in spec and name == spec):
            return spec
    return None


class GitScope:
    """One Git work tree and the paths Git does not ignore."""

    def __init__(self, root: Path, entries: set[str]) -> None:
        self.root = root
        self._entries = entries
        self._directories = {
            "/".join(segments[:depth])
            for entry in entries
            for segments in (entry.split("/")[:-1],)
            for depth in range(1, len(segments) + 1)
        }

    def ignores(self, path: Path, is_directory: bool) -> bool:
        relative = path.relative_to(self.root).as_posix()
        if is_directory:
            # A submodule appears as a single entry, so keep it walkable.
            return relative not in self._directories and relative not in self._entries
        return relative not in self._entries


def git_scope(root: Path) -> GitScope | None:
    """Ask Git for the target's ignore semantics instead of reimplementing them."""
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
            ],
            check=True,
            capture_output=True,
            encoding="utf-8",
            errors="surrogateescape",
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return GitScope(root, {entry for entry in result.stdout.split("\0") if entry})


def nested_git_scope(directory: Path, inherited: GitScope | None) -> GitScope | None:
    """Give a nested work tree its own ignore scope."""
    if not (directory / ".git").exists():
        return inherited
    return git_scope(directory) or inherited


def classify_text(path: Path) -> tuple[bool, str]:
    try:
        with path.open("rb") as handle:
            sample = handle.read(64 * 1024)
    except OSError as exc:
        return False, f"unreadable:{exc.__class__.__name__}"
    if not sample:
        return True, "empty_text"
    if b"\x00" in sample:
        return False, "binary_nul"
    decoded = sample.decode("utf-8", errors="replace")
    replacement_ratio = decoded.count("\ufffd") / max(1, len(decoded))
    if replacement_ratio > 0.01:
        return False, "non_utf8_or_binary"
    return True, "utf8_text"


class NotDeliverable(Exception):
    """A path that cannot be delivered to a scout as snapshot text."""

    def __init__(self, kind: str, reason: str) -> None:
        super().__init__(reason)
        self.kind = kind
        self.reason = reason


@dataclass(frozen=True)
class ExclusionPolicy:
    """Own the normal layers that keep paths out of the snapshot."""

    extra_globs: list[str]
    excluded_specs: set[str]

    def directory_reason(
        self, path: Path, name: str, relative: str, scope: GitScope | None
    ) -> str | None:
        spec = matched_path_spec(name, relative, self.excluded_specs)
        if spec is not None:
            return f"user_excluded_directory:{spec}"
        if name in DEFAULT_EXCLUDED_DIR_NAMES:
            return f"default_excluded_directory:{name}"
        if scope is not None and scope.ignores(path, True):
            return "gitignored"
        return None

    def file_reason(
        self, path: Path, name: str, relative: str, scope: GitScope | None
    ) -> str | None:
        spec = matched_path_spec(name, relative, self.excluded_specs)
        if spec is not None:
            return f"user_excluded_file:{spec}"
        if name in DEFAULT_EXCLUDED_FILE_NAMES:
            return "generated_dependency_lock"
        for pattern in (*DEFAULT_EXCLUDED_GLOBS, *self.extra_globs):
            if fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(name, pattern):
                return f"excluded_glob:{pattern}"
        if scope is not None and scope.ignores(path, False):
            return "gitignored"
        return None


def snapshot_record(path: Path, relative: str) -> dict[str, Any]:
    """Hash one readable regular text file for the snapshot."""
    try:
        mode = path.stat().st_mode
    except OSError as exc:
        raise NotDeliverable("file", f"unreadable:{exc.__class__.__name__}") from exc
    if not stat.S_ISREG(mode):
        raise NotDeliverable("special", "not_a_regular_file")
    is_text, text_reason = classify_text(path)
    if not is_text:
        raise NotDeliverable("file", text_reason)
    try:
        size = path.stat().st_size
        digest = sha256_file(path)
    except OSError as exc:
        raise NotDeliverable("file", f"unreadable:{exc.__class__.__name__}") from exc
    return {
        "path": relative,
        "bytes": size,
        "estimated_tokens": estimated_numbered_tokens(path),
        "sha256": digest,
        "classification": text_reason,
    }


def inventory(
    root: Path,
    output_parent: Path,
    extra_globs: list[str],
    excluded_specs: set[str],
    only_specs: set[str],
    force_include_specs: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    exclusions = ExclusionPolicy(extra_globs, excluded_specs)
    scope = ScopePolicy(only_specs)
    force_includes = ForceIncludePolicy(force_include_specs)
    files: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    output_inside_root = root in output_parent.parents
    git_scopes: dict[str, GitScope | None] = {str(root): git_scope(root)}
    inherited_exclusions: dict[str, str | None] = {str(root): None}

    def relative_to_root(path: Path) -> str:
        try:
            return path.relative_to(root).as_posix() or "."
        except ValueError:
            return path.name

    def record_excluded(path: Path, kind: str, reason: str) -> None:
        excluded.append(
            {"path": relative_to_root(path), "kind": kind, "reason": reason}
        )

    def is_scan_output(path: Path) -> bool:
        return output_inside_root and (
            path == output_parent or output_parent in path.parents
        )

    for current_raw, dir_names, file_names in os.walk(
        root, topdown=True, followlinks=False
    ):
        current = Path(current_raw)
        dir_names.sort()
        file_names.sort()
        git = git_scopes.pop(str(current), None)
        inherited_exclusion = inherited_exclusions.pop(str(current), None)

        kept_dirs: list[str] = []
        for name in dir_names:
            path = current / name
            relative = relative_to_root(path)
            if path.is_symlink():
                record_excluded(path, "symlink_directory", "symlink_not_followed")
                continue
            if is_scan_output(path):
                record_excluded(path, "directory", "scan_output_directory")
                continue
            if scope.relation(relative) == "outside":
                record_excluded(path, "directory", "outside_only_scope")
                continue
            force_relation = force_includes.relation(relative)
            reason = inherited_exclusion or exclusions.directory_reason(
                path, name, relative, git
            )
            if force_relation == "outside" and reason is not None:
                record_excluded(path, "directory", reason)
                continue
            kept_dirs.append(name)
            git_scopes[str(path)] = nested_git_scope(path, git)
            inherited_exclusions[str(path)] = (
                reason if force_relation == "ancestor" else None
            )
        dir_names[:] = kept_dirs

        for name in file_names:
            path = current / name
            relative = relative_to_root(path)
            if path.is_symlink():
                record_excluded(path, "symlink_file", "symlink_not_followed")
                continue
            if scope.relation(relative) != "selected":
                record_excluded(path, "file", "outside_only_scope")
                continue
            if force_includes.relation(relative) != "selected":
                reason = inherited_exclusion or exclusions.file_reason(
                    path, name, relative, git
                )
                if reason is not None:
                    record_excluded(path, "file", reason)
                    continue
            try:
                files.append(snapshot_record(path, relative))
            except NotDeliverable as exc:
                record_excluded(path, exc.kind, exc.reason)

    files.sort(key=lambda item: item["path"])
    excluded.sort(key=lambda item: (item["path"], item["kind"], item["reason"]))
    return files, excluded
