"""Own the private workspace and derived artifact locations for one scan."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path


TOOL_DIRECTORY = "askeveryfile"
WORKSPACE_IDENTITY_SCHEMA_VERSION = 1
TOPIC_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
REDUCTION_DIRECTORY_PATTERN = re.compile(
    r"^reduction(?:-[a-z0-9]+(?:-[a-z0-9]+)*)?$"
)
UNSAFE_COMPONENTS = re.compile(r"[^a-zA-Z0-9._-]+")
IDENTITY_READY_TIMEOUT_SECONDS = 2.0
IDENTITY_READY_POLL_SECONDS = 0.02


class WorkspaceError(ValueError):
    """An expected workspace request failure."""


@dataclass(frozen=True)
class WorkspaceResult:
    target: Path
    repository_root: Path | None
    workspace: Path
    identity: Path
    question_file: Path
    scan_parent: Path
    created: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "target": str(self.target),
            "repository_root": (
                str(self.repository_root) if self.repository_root is not None else None
            ),
            "workspace": str(self.workspace),
            "question_file": str(self.question_file),
            "scan_parent": str(self.scan_parent),
            "created": self.created,
        }


def _is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _absolute_path(path_input: str | Path) -> Path:
    return Path(os.path.abspath(os.fspath(Path(path_input).expanduser())))


def _canonical_directory(path_input: str | Path, label: str) -> Path:
    try:
        path = Path(path_input).expanduser().resolve(strict=True)
    except OSError as error:
        raise WorkspaceError(f"{label} does not exist: {path_input}") from error
    if not path.is_dir():
        raise WorkspaceError(f"{label} must be a directory: {path}")
    return path


def _repository_root(target: Path) -> Path | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(target), "rev-parse", "--show-toplevel"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except FileNotFoundError:
        return None
    if completed.returncode != 0:
        return None
    root = _canonical_directory(completed.stdout.strip(), "Git root")
    if not _is_within(target, root):
        raise WorkspaceError("Resolved Git root does not contain the target")
    return root


def _target_key(target: Path) -> str:
    basename = UNSAFE_COMPONENTS.sub("-", target.name).strip("-.").lower()
    path_hash = hashlib.sha256(os.fsencode(str(target))).hexdigest()[:12]
    return f"{basename or 'target'}-{path_hash}"


def _reject_symlink(path: Path) -> None:
    if path.is_symlink():
        raise WorkspaceError(f"Workspace component must not be a symlink: {path}")


def _ensure_private_directory(path: Path, *, parents: bool = False) -> bool:
    _reject_symlink(path)
    try:
        path.mkdir(mode=0o700, parents=parents)
        created = True
    except FileExistsError:
        _reject_symlink(path)
        if not path.is_dir():
            raise WorkspaceError(f"Workspace component is not a directory: {path}")
        created = False
    path.chmod(0o700)
    return created


def _publish_identity(path: Path, identity: dict[str, object]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".identity-", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            json.dump(identity, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _read_identity(path: Path) -> object:
    _reject_symlink(path)
    if not path.is_file():
        raise WorkspaceError(f"Existing workspace lacks identity: {path.parent}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WorkspaceError(f"Cannot verify workspace identity: {path}") from error


def _wait_for_identity(path: Path) -> None:
    deadline = time.monotonic() + IDENTITY_READY_TIMEOUT_SECONDS
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(IDENTITY_READY_POLL_SECONDS)


def _expected_identity(
    target: Path, repository_root: Path | None, topic: str
) -> dict[str, object]:
    return {
        "schema_version": WORKSPACE_IDENTITY_SCHEMA_VERSION,
        "target": str(target),
        "repository_root": (
            str(repository_root) if repository_root is not None else None
        ),
        "topic": topic,
    }


def _temp_root() -> Path:
    return _canonical_directory(tempfile.gettempdir(), "Temporary root")


def _workspace_paths(
    target: Path, repository_root: Path | None, topic: str
) -> WorkspaceResult:
    temp_root = _temp_root()
    tool_root = temp_root / TOOL_DIRECTORY
    boundary = repository_root or target
    if _is_within(tool_root, boundary) or _is_within(boundary, tool_root):
        raise WorkspaceError(
            "Temporary workspace root must be outside the target and its Git repository"
        )
    workspace = tool_root / _target_key(target) / topic
    return WorkspaceResult(
        target=target,
        repository_root=repository_root,
        workspace=workspace,
        identity=workspace / "identity.json",
        question_file=workspace / "question.md",
        scan_parent=workspace / "scans",
        created=False,
    )


def _verify_identity(result: WorkspaceResult, topic: str) -> None:
    expected = _expected_identity(result.target, result.repository_root, topic)
    if _read_identity(result.identity) != expected:
        raise WorkspaceError(f"Workspace identity does not match: {result.workspace}")


def _await_workspace_identity(result: WorkspaceResult, topic: str) -> None:
    _wait_for_identity(result.identity)
    _verify_identity(result, topic)


def prepare_workspace(target_input: str | Path, topic: str) -> WorkspaceResult:
    if not TOPIC_PATTERN.fullmatch(topic):
        raise WorkspaceError(
            "Topic must contain lowercase letters and digits separated by single hyphens"
        )
    target = _canonical_directory(target_input, "Target")
    repository_root = _repository_root(target)
    result = _workspace_paths(target, repository_root, topic)
    tool_root = result.workspace.parents[1]
    target_workspace = result.workspace.parent
    for path in (
        tool_root,
        target_workspace,
        result.workspace,
        result.identity,
        result.question_file,
        result.scan_parent,
    ):
        _reject_symlink(path)
    _ensure_private_directory(tool_root, parents=True)
    _ensure_private_directory(target_workspace)
    created = _ensure_private_directory(result.workspace)
    if created:
        _publish_identity(
            result.identity,
            _expected_identity(target, repository_root, topic),
        )
    else:
        _await_workspace_identity(result, topic)
    _ensure_private_directory(result.scan_parent)
    return replace(result, created=created)


def _canonical_workspace_directory(
    path_input: str | Path, temp_root: Path, label: str
) -> Path:
    lexical = _absolute_path(path_input)
    try:
        relative = lexical.relative_to(temp_root)
    except ValueError as error:
        raise WorkspaceError(f"{label} must be inside the OS temporary directory") from error
    current = temp_root
    for component in relative.parts:
        current /= component
        _reject_symlink(current)
    return _canonical_directory(lexical, label)


def validate_scan_parent(
    target_input: str | Path, scan_parent_input: str | Path
) -> WorkspaceResult:
    target = _canonical_directory(target_input, "Target")
    repository_root = _repository_root(target)
    temp_root = _temp_root()
    scan_parent = _canonical_workspace_directory(
        scan_parent_input, temp_root, "Scan parent"
    )
    workspace = scan_parent.parent
    topic = workspace.name
    if scan_parent.name != "scans" or not TOPIC_PATTERN.fullmatch(topic):
        raise WorkspaceError(
            "--output-dir must be the scan_parent returned by prepare_workspace.py"
        )
    expected = _workspace_paths(target, repository_root, topic)
    if scan_parent != expected.scan_parent:
        raise WorkspaceError(
            "--output-dir must be the scan_parent returned by prepare_workspace.py"
        )
    _verify_identity(expected, topic)
    return expected


def validate_question_file(
    question_file_input: str | Path, workspace: WorkspaceResult
) -> Path:
    lexical = _absolute_path(question_file_input)
    _reject_symlink(lexical)
    try:
        question_file = lexical.resolve(strict=True)
    except OSError as error:
        raise WorkspaceError(f"Question file does not exist: {lexical}") from error
    if question_file != workspace.question_file:
        raise WorkspaceError(
            "--question-file must be the question_file returned by prepare_workspace.py"
        )
    if not question_file.is_file():
        raise WorkspaceError(f"Question file must be a file: {question_file}")
    return question_file


def review_packet_output(
    scan_dir: Path,
    output_input: str | Path | None,
    read_only_target: Path,
) -> Path:
    expected = scan_dir.resolve(strict=True) / "review-packet.json"
    if _is_within(expected, read_only_target.resolve(strict=True)):
        raise WorkspaceError(
            f"Review packet output must be outside the read-only target: {expected}"
        )
    if output_input is None:
        return expected
    requested = _absolute_path(output_input)
    _reject_symlink(requested)
    if requested.resolve() != expected:
        raise WorkspaceError(
            f"Review packet output is fixed to the scan directory: {expected}"
        )
    return expected


def reduction_output(
    scan_dir: Path,
    output_input: str | Path | None,
    read_only_target: Path,
) -> Path:
    canonical_scan_dir = scan_dir.resolve(strict=True)
    candidate = (
        canonical_scan_dir / "reduction"
        if output_input is None
        else _absolute_path(output_input)
    )
    _reject_symlink(candidate)
    output = candidate.resolve()
    if (
        output.parent != canonical_scan_dir
        or not REDUCTION_DIRECTORY_PATTERN.fullmatch(output.name)
    ):
        raise WorkspaceError(
            "Reduction output must be reduction or a named reduction-* directory "
            f"directly inside the scan directory: {canonical_scan_dir}"
        )
    # A disappeared source is stale evidence, not an unowned output location.
    # The original root still defines the write exclusion boundary.
    if _is_within(output, read_only_target.resolve()):
        raise WorkspaceError(
            f"Reduction output must be outside the read-only target: {output}"
        )
    return output
