import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.run_ue_benchmark import run_native, classify
from scripts.run_ue_smoke_test import main as smoke_main
from unreal_harness.execution import FileExecutionTransport, UnrealExecutionError


class NativeRunnerTests(unittest.TestCase):
    def test_pending_and_concurrent_tasks_protect_both_native_runners(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            project = root / "Test.uproject"
            project.write_text("{}")
            transport = FileExecutionTransport(root)
            transport.input_path.parent.mkdir(parents=True)
            transport.input_path.write_bytes(b"task actions")
            transport.result_path.write_bytes(b"task result")
            transport.pending_path.write_bytes(b"pending evidence")
            runners = (
                lambda: run_native({"commands": []}, project, None),
                lambda: smoke_main(["--project", str(project)]),
            )
            with patch("subprocess.run") as launch:
                for runner in runners:
                    with self.assertRaisesRegex(UnrealExecutionError, "Pending harness"):
                        runner()
                self.assertEqual(transport.pending_path.read_bytes(), b"pending evidence")
                transport.pending_path.unlink()
                with transport.session():
                    for runner in runners:
                        with self.assertRaisesRegex(UnrealExecutionError, "Another harness task"):
                            runner()
                launch.assert_not_called()
            self.assertEqual(transport.input_path.read_bytes(), b"task actions")
            self.assertEqual(transport.result_path.read_bytes(), b"task result")

    def test_failed_commandlet_preserves_known_unsupported_result_and_mailbox(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            project = root / "Test.uproject"
            project.write_text("{}")
            editor = root / "Editor"
            editor.write_bytes(b"fixture executable")
            python = root / "Content/Python"
            python.mkdir(parents=True)
            (python / "execute_actions.py").write_text("# fixture")
            actions, result = python / "actions.json", python / "result.json"
            actions.write_text("original actions")
            result.write_text("original result")
            task = {"category": "unsupported", "document": {"commands": [{"id": "bad", "action": "unsupported.action"}]}}
            def execute(command, **kwargs):
                packet = json.loads(actions.read_text())
                result.write_text(json.dumps({"run_id": packet["run_id"], "success": False,
                                             "commands": [], "errors": [{"code": "unknown_action"}]}))
                self.assertNotIn("\\", command[3].removeprefix("-script="))
                return subprocess.CompletedProcess(command, 1)
            with patch("scripts.run_ue_benchmark.subprocess.run", side_effect=execute):
                response = run_native(task["document"], project, editor)
            self.assertEqual(classify(task, response, native=True), "unsupported")
            self.assertEqual(actions.read_text(), "original actions")
            self.assertEqual(result.read_text(), "original result")
