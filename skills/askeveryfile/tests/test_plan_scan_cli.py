from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLANNER = Path(__file__).resolve().parents[1] / "scripts" / "plan_scan.py"
sys.path.insert(0, str(PLANNER.parent))

from scan_workspace import prepare_workspace  # noqa: E402


class PlanScanCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.os_temp = self.base / "os-temp"
        self.os_temp.mkdir()
        self.previous_tempdir = tempfile.tempdir
        tempfile.tempdir = str(self.os_temp)
        self.env = os.environ.copy()
        self.env["TMPDIR"] = str(self.os_temp)
        self.root = self.base / "target"
        self.root.mkdir()
        self.run_number = 0

    def tearDown(self) -> None:
        tempfile.tempdir = self.previous_tempdir
        self.temp_dir.cleanup()

    def write(self, relative: str, text: str = "evidence\n") -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def command(self, *options: str) -> list[str]:
        self.run_number += 1
        workspace = prepare_workspace(self.root, f"test-{self.run_number}")
        return [
            sys.executable,
            str(PLANNER),
            str(self.root),
            "--output-dir",
            str(workspace.scan_parent),
            "--question",
            "Identify relevant evidence in this shard.",
            *options,
        ]

    def run_plan(self, *options: str) -> tuple[dict, dict]:
        result = subprocess.run(
            self.command(*options),
            check=True,
            capture_output=True,
            text=True,
            env=self.env,
        )
        summary = json.loads(result.stdout)
        plan = json.loads(Path(summary["plan"]).read_text(encoding="utf-8"))
        return summary, plan

    def test_only_restricts_scope_and_reports_top_level_counts(self) -> None:
        self.write("README.md")
        self.write("src/app.py")
        self.write("tests/test_app.py")
        self.write("docs/guide.md")
        self.write("evidence/raw.md")

        summary, plan = self.run_plan("--only", "src", "tests", "docs")

        self.assertEqual(
            ["docs/guide.md", "src/app.py", "tests/test_app.py"],
            [item["path"] for item in plan["files"]],
        )
        self.assertEqual("only", summary["scope"]["mode"])
        self.assertEqual(["docs", "src", "tests"], summary["scope"]["only_paths"])
        self.assertEqual(
            {"docs": 1, "src": 1, "tests": 1},
            summary["included_text_files_by_top_level"],
        )
        self.assertIn("outside_only_scope", plan["coverage"]["excluded_by_reason"])

    def test_force_include_does_not_restrict_scope(self) -> None:
        self.write("src/app.py")
        self.write("evidence/raw.md")

        summary, plan = self.run_plan("--force-include", "src")

        self.assertEqual(
            ["evidence/raw.md", "src/app.py"],
            [item["path"] for item in plan["files"]],
        )
        self.assertEqual("all", summary["scope"]["mode"])
        self.assertEqual(["src"], summary["force_included_paths"])
        self.assertIn("does not restrict scope", summary["scope_note"])

    def test_force_include_reaches_one_file_below_excluded_ancestor(self) -> None:
        self.write("root.md")
        self.write(".letsworkitout/find-by-meaning/work.md")
        self.write(".letsworkitout/find-by-meaning/sibling.md")

        _, plan = self.run_plan(
            "--force-include", ".letsworkitout/find-by-meaning/work.md"
        )

        self.assertEqual(
            [".letsworkitout/find-by-meaning/work.md", "root.md"],
            [item["path"] for item in plan["files"]],
        )
        sibling = next(
            item
            for item in plan["excluded"]
            if item["path"] == ".letsworkitout/find-by-meaning/sibling.md"
        )
        self.assertEqual("default_excluded_directory:.letsworkitout", sibling["reason"])

    def test_force_include_reaches_one_file_below_gitignored_ancestor(self) -> None:
        self.write(".gitignore", "ignored/\n")
        self.write("ignored/wanted.md")
        self.write("ignored/sibling.md")
        subprocess.run(
            ["git", "init", "--quiet", str(self.root)],
            check=True,
            capture_output=True,
            text=True,
        )

        _, plan = self.run_plan("--force-include", "ignored/wanted.md")

        self.assertEqual(
            [".gitignore", "ignored/wanted.md"],
            [item["path"] for item in plan["files"]],
        )
        sibling = next(
            item for item in plan["excluded"] if item["path"] == "ignored/sibling.md"
        )
        self.assertEqual("gitignored", sibling["reason"])

    def test_only_and_force_include_compose_without_widening(self) -> None:
        selected = ".letsworkitout/find-by-meaning/work.md"
        self.write(selected)
        self.write(".letsworkitout/find-by-meaning/sibling.md")
        self.write("outside.md")

        _, plan = self.run_plan("--only", selected, "--force-include", selected)

        self.assertEqual([selected], [item["path"] for item in plan["files"]])

    def test_only_does_not_override_normal_exclusions(self) -> None:
        self.write(".letsworkitout/work.md")

        _, plan = self.run_plan("--only", ".letsworkitout")

        self.assertEqual([], plan["files"])
        excluded = next(
            item for item in plan["excluded"] if item["path"] == ".letsworkitout"
        )
        self.assertEqual(
            "default_excluded_directory:.letsworkitout", excluded["reason"]
        )

    def test_force_include_outside_only_scope_is_rejected(self) -> None:
        self.write("src/app.py")
        self.write("docs/guide.md")

        result = subprocess.run(
            self.command("--only", "src", "--force-include", "docs"),
            capture_output=True,
            text=True,
            check=False,
            env=self.env,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("outside --only scope", result.stderr)

    def test_removed_include_option_is_rejected(self) -> None:
        self.write("src/app.py")

        result = subprocess.run(
            self.command("--include", "src"),
            capture_output=True,
            text=True,
            check=False,
            env=self.env,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("unrecognized arguments: --include", result.stderr)

    def test_repo_local_output_is_rejected_without_creating_it(self) -> None:
        source = self.root / "src"
        source.mkdir()
        (source / "app.py").write_text("evidence\n", encoding="utf-8")
        subprocess.run(
            ["git", "init", "--quiet", str(self.root)],
            check=True,
            capture_output=True,
            text=True,
        )
        output = self.root / ".askeveryfile" / "scan-output"

        result = subprocess.run(
            [
                sys.executable,
                str(PLANNER),
                str(source),
                "--output-dir",
                str(output),
                "--question",
                "Identify relevant evidence in this shard.",
            ],
            capture_output=True,
            text=True,
            check=False,
            env=self.env,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("inside the OS temporary directory", result.stderr)
        self.assertFalse(output.exists())

    def test_question_file_must_belong_to_prepared_workspace(self) -> None:
        self.write("src/app.py")
        workspace = prepare_workspace(self.root, "question-path")
        unrelated = self.base / "question.md"
        unrelated.write_text("Identify relevant evidence.\n", encoding="utf-8")

        result = subprocess.run(
            [
                sys.executable,
                str(PLANNER),
                str(self.root),
                "--output-dir",
                str(workspace.scan_parent),
                "--question-file",
                str(unrelated),
            ],
            capture_output=True,
            text=True,
            check=False,
            env=self.env,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("question_file returned by prepare_workspace.py", result.stderr)

    def test_prepared_question_file_plans_without_touching_git_worktree(self) -> None:
        source = self.root / "src"
        source.mkdir()
        (source / "app.py").write_text("evidence\n", encoding="utf-8")
        subprocess.run(
            ["git", "init", "--quiet", str(self.root)],
            check=True,
            capture_output=True,
            text=True,
        )
        workspace = prepare_workspace(source, "prepared-question")
        workspace.question_file.write_text(
            "Identify relevant evidence in this shard.\n", encoding="utf-8"
        )
        before = subprocess.run(
            ["git", "-C", str(self.root), "status", "--short"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout

        result = subprocess.run(
            [
                sys.executable,
                str(PLANNER),
                str(source),
                "--output-dir",
                str(workspace.scan_parent),
                "--question-file",
                str(workspace.question_file),
            ],
            check=True,
            capture_output=True,
            text=True,
            env=self.env,
        )

        summary = json.loads(result.stdout)
        plan_path = Path(summary["plan"])
        self.assertIn(self.os_temp.resolve(), plan_path.parents)
        self.assertNotIn(self.root.resolve(), plan_path.parents)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(
            "Identify relevant evidence in this shard.\n", plan["question"]
        )
        after = subprocess.run(
            ["git", "-C", str(self.root), "status", "--short"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
