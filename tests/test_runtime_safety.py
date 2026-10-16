import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from unreal_harness.execution import FileExecutionTransport, UnrealExecutionError, validate_response
from unreal_harness.compatibility import validate_runtime
from unreal_harness.task_agent import TaskAgent
from test_task_agent import World, Planner


class RuntimeSafetyTests(unittest.TestCase):
    def test_invalid_timeout_cannot_publish(self):
        with tempfile.TemporaryDirectory() as root:
            for value in (0, -1, float("nan"), float("inf"), True):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    FileExecutionTransport(root, timeout=value)
            transport = FileExecutionTransport(root)
            with self.assertRaises(ValueError):
                transport.submit({"commands": []}, timeout=math.nan)
            self.assertFalse(transport.input_path.exists())

    def test_malformed_late_reply_cannot_clear_pending(self):
        with tempfile.TemporaryDirectory() as root:
            transport = FileExecutionTransport(root, timeout=0.01, poll_interval=0.001)
            with self.assertRaises(UnrealExecutionError):
                transport.submit({"commands": []})
            pending = json.loads(transport.pending_path.read_text())
            original = transport.input_path.read_bytes()
            for reply in ({"run_id": pending["run_id"]}, {"run_id": pending["run_id"], "success": True, "commands": [], "execution_status": "unknown"}):
                transport.result_path.write_text(json.dumps(reply))
                with self.assertRaisesRegex(UnrealExecutionError, "Previous request"):
                    transport.submit({"commands": []})
                self.assertTrue(transport.pending_path.exists())
                self.assertEqual(transport.input_path.read_bytes(), original)

    def test_success_must_include_unique_successful_command_results(self):
        document = {"run_id": "test", "commands": [{"id": "read", "action": "content.list", "arguments": {}}]}
        for commands in ([], [{"id": "read", "success": False}], [{"id": "read", "success": True}] * 2):
            with self.subTest(commands=commands), self.assertRaises(UnrealExecutionError):
                validate_response({"run_id": "test", "success": True, "commands": commands}, document)

    def test_dryrun_empty_result_and_failed_validation_are_known_outcomes(self):
        document = {"run_id": "test", "dry_run": True, "commands": [{"id": "read", "action": "content.list"}]}
        validate_response({"run_id": "test", "success": True, "commands": []}, document)
        validate_response({"run_id": "test", "success": False, "commands": []}, dict(document, dry_run=False))

    def test_invalid_changed_objects_and_correlation_rejected(self):
        for extra in ({"changed_objects": "not a list"}, {"run_id": "wrong"}):
            with self.assertRaises(UnrealExecutionError):
                validate_response({"run_id": "test", "success": True, "commands": [], **extra}, {"run_id": "test", "commands": []})

    def test_incompatible_caps_stop_before_model(self):
        world, planner = World(), Planner([])
        original = world.submit
        def submit(document, **kwargs):
            result = original(document, **kwargs)
            result["commands"][0]["data"]["engine_version"] = "5.7.4"
            return result
        with patch.object(world, "submit", side_effect=submit):
            result = TaskAgent(world, planner).run("inspect")
        self.assertFalse(result["success"])
        self.assertIn("expected 5.8", result["error"])
        self.assertEqual(planner.contexts, [])

    def test_engine_and_contract_match_exactly(self):
        validate_runtime({"engine_version": "5.8.3-123+++UE5", "contract_version": "1.0"})
        for data in ({"engine_version": "5.80"}, {"engine_version": "6.0"}, {"contract_version": "2.0"}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                validate_runtime(data)
