from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import scan_workspace  # noqa: E402
from scan_workspace import (  # noqa: E402
    WorkspaceError,
    prepare_workspace,
    reduction_output,
    review_packet_output,
    validate_scan_parent,
)


class PrepareWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.temp_root = self.base / "os-temp"
        self.temp_root.mkdir()
        self.previous_tempdir = tempfile.tempdir
        tempfile.tempdir = str(self.temp_root)
        self.target = self.base / "target"
        self.target.mkdir()

    def tearDown(self) -> None:
        tempfile.tempdir = self.previous_tempdir
        self.temporary.cleanup()

    def test_prepares_private_workspace_and_resumes_matching_identity(self) -> None:
        first = prepare_workspace(self.target, "purpose-audit")
        second = prepare_workspace(self.target, "purpose-audit")

        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.workspace, second.workspace)
        self.assertEqual(first.scan_parent, second.scan_parent)
        self.assertEqual(0o700, first.workspace.stat().st_mode & 0o777)
        self.assertEqual(0o700, first.scan_parent.stat().st_mode & 0o777)
        self.assertEqual(0o600, first.identity.stat().st_mode & 0o777)
        identity = json.loads(first.identity.read_text(encoding="utf-8"))
        self.assertEqual(str(self.target.resolve()), identity["target"])
        self.assertEqual("purpose-audit", identity["topic"])
        validated = validate_scan_parent(self.target, first.scan_parent)
        self.assertEqual(first.workspace, validated.workspace)
        self.assertFalse(validated.created)

    def test_nested_git_target_stays_exact_and_workspace_stays_outside_repo(self) -> None:
        repo = self.base / "repo"
        target = repo / "src"
        target.mkdir(parents=True)
        subprocess.run(
            ["git", "init", "--quiet", str(repo)],
            check=True,
            capture_output=True,
            text=True,
        )

        result = prepare_workspace(target, "nested-target")

        self.assertEqual(target.resolve(), result.target)
        self.assertEqual(repo.resolve(), result.repository_root)
        self.assertNotIn(repo.resolve(), result.workspace.parents)
        status = subprocess.run(
            ["git", "-C", str(repo), "status", "--short"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual("", status.stdout)

    def test_same_basename_targets_have_distinct_workspaces(self) -> None:
        first_target = self.base / "first" / "project"
        second_target = self.base / "second" / "project"
        first_target.mkdir(parents=True)
        second_target.mkdir(parents=True)

        first = prepare_workspace(first_target, "same-topic")
        second = prepare_workspace(second_target, "same-topic")

        self.assertNotEqual(first.workspace, second.workspace)

    def test_concurrent_preparation_waits_for_complete_identity(self) -> None:
        entered_identity_write = threading.Event()
        release_identity_write = threading.Event()
        original_publish_identity = scan_workspace._publish_identity
        results = []
        errors = []

        def delayed_publish_identity(path: Path, identity: dict[str, object]) -> None:
            entered_identity_write.set()
            release_identity_write.wait(timeout=1)
            original_publish_identity(path, identity)

        def prepare() -> None:
            try:
                results.append(prepare_workspace(self.target, "concurrent"))
            except Exception as error:  # noqa: BLE001 - preserves thread failure
                errors.append(error)

        with mock.patch.object(
            scan_workspace,
            "_publish_identity",
            side_effect=delayed_publish_identity,
        ):
            first = threading.Thread(target=prepare)
            second = threading.Thread(target=prepare)
            first.start()
            self.assertTrue(entered_identity_write.wait(timeout=1))
            second.start()
            second.join(timeout=0.1)
            self.assertTrue(second.is_alive())
            release_identity_write.set()
            first.join(timeout=1)
            second.join(timeout=1)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual([], errors)
        self.assertEqual([False, True], sorted(result.created for result in results))

    def test_temporary_root_inside_target_is_rejected(self) -> None:
        nested_temp = self.target / "tmp"
        nested_temp.mkdir()
        tempfile.tempdir = str(nested_temp)

        with self.assertRaisesRegex(
            WorkspaceError, "outside the target and its Git repository"
        ):
            prepare_workspace(self.target, "unsafe-temp")

    def test_symlinked_tool_root_is_rejected(self) -> None:
        destination = self.base / "redirected"
        destination.mkdir()
        (self.temp_root / "askeveryfile").symlink_to(destination, target_is_directory=True)

        with self.assertRaisesRegex(WorkspaceError, "must not be a symlink"):
            prepare_workspace(self.target, "symlinked-root")

    def test_repo_local_scan_parent_is_rejected_without_creation(self) -> None:
        repo = self.base / "repo-local"
        target = repo / "src"
        target.mkdir(parents=True)
        subprocess.run(
            ["git", "init", "--quiet", str(repo)],
            check=True,
            capture_output=True,
            text=True,
        )
        local_parent = repo / ".askeveryfile" / "topic" / "scans"

        with self.assertRaisesRegex(
            WorkspaceError, "inside the OS temporary directory"
        ):
            validate_scan_parent(target, local_parent)

        self.assertFalse(local_parent.exists())

    def test_changed_identity_is_rejected(self) -> None:
        result = prepare_workspace(self.target, "identity-check")
        result.identity.write_text("{}\n", encoding="utf-8")

        with self.assertRaisesRegex(WorkspaceError, "identity does not match"):
            prepare_workspace(self.target, "identity-check")

    def test_derived_outputs_preserve_the_read_only_target_boundary(self) -> None:
        scan_dir = self.target / "legacy-scan"
        scan_dir.mkdir()

        with self.assertRaisesRegex(WorkspaceError, "outside the read-only target"):
            review_packet_output(scan_dir, None, self.target)
        with self.assertRaisesRegex(WorkspaceError, "outside the read-only target"):
            reduction_output(scan_dir, None, self.target)


if __name__ == "__main__":
    unittest.main()
