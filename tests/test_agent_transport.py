import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from unreal_harness import AgentRun, FileExecutionTransport, UnrealExecutionError


class AgentTransportTests(unittest.TestCase):
    def test_agent_reserves_mailbox_while_model_plans(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = FileExecutionTransport(directory, timeout=1, poll_interval=0.001)
            other = FileExecutionTransport(directory, timeout=0.01)
            stop = threading.Event()
            def watcher():
                previous = None
                while not stop.is_set():
                    if transport.input_path.exists():
                        document = json.loads(transport.input_path.read_text())
                        if document["run_id"] != previous:
                            previous = document["run_id"]
                            commands = [{"id": c["id"], "success": True, "data": {"actions": ["level.inspect"]} if c["action"] == "system.capabilities" else {"actors": []}} for c in document["commands"]]
                            transport.result_path.write_text(json.dumps({"run_id": previous, "success": True, "commands": commands}))
                    stop.wait(0.001)
            def planner(task, context):
                with self.assertRaisesRegex(UnrealExecutionError, "Another harness task"):
                    other.submit({"commands": []})
                return {"commands": [{"id": "read", "action": "level.inspect"}]}
            thread = threading.Thread(target=watcher)
            thread.start()
            try:
                result = AgentRun(transport, planner).run("inspect")
                self.assertTrue(result["success"], result)
            finally:
                stop.set()
                thread.join(timeout=2)

    def test_project_session_blocks_other_transport_and_supports_nested_submit(self):
        with tempfile.TemporaryDirectory() as directory:
            first = FileExecutionTransport(directory, timeout=0.01, poll_interval=0.001)
            second = FileExecutionTransport(directory, timeout=0.01, poll_interval=0.001)
            with first.session():
                with self.assertRaisesRegex(UnrealExecutionError, "Another harness task"):
                    second.submit({"commands": []})
                self.assertFalse(first.input_path.exists())
                with first.session():
                    with self.assertRaisesRegex(UnrealExecutionError, "Timed out"):
                        first.submit({"commands": []})
            with second.session():
                pass  # OS lock released even when submission fails.

    def test_timeout_retains_request_and_blocks_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = FileExecutionTransport(directory, timeout=0.01, poll_interval=0.001)
            with self.assertRaisesRegex(UnrealExecutionError, "status is unknown"):
                transport.submit({"commands": []})
            original = transport.input_path.read_bytes()
            self.assertTrue(transport.pending_path.exists())
            with self.assertRaisesRegex(UnrealExecutionError, "Previous request"):
                FileExecutionTransport(directory).submit({"commands": []})
            self.assertEqual(transport.input_path.read_bytes(), original)

    def test_late_response_is_archived_before_next_request(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = FileExecutionTransport(directory, timeout=0.01, poll_interval=0.001)
            with self.assertRaises(UnrealExecutionError):
                transport.submit({"commands": []})
            old = json.loads(transport.pending_path.read_text())
            transport.result_path.write_text(json.dumps({"run_id": old["run_id"], "success": True, "commands": []}))
            def watcher():
                for _ in range(200):
                    document = json.loads(transport.input_path.read_text())
                    if document["run_id"] != old["run_id"]:
                        transport.result_path.write_text(json.dumps({"run_id": document["run_id"], "success": True, "commands": []}))
                        return
                    time.sleep(0.005)
            thread = threading.Thread(target=watcher)
            thread.start()
            try:
                result = transport.submit({"commands": []}, timeout=1)
            finally:
                thread.join(timeout=2)
            self.assertTrue(result["success"])
            self.assertFalse(transport.pending_path.exists())
            audits = [json.loads(path.read_text()) for path in (Path(directory) / "Saved/HarnessAudit").glob("*.json")]
            self.assertEqual({a["result"]["run_id"] for a in audits}, {old["run_id"], result["run_id"]})

    def test_non_object_response_is_ignored_until_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = FileExecutionTransport(directory, timeout=0.04, poll_interval=0.001)
            def watcher():
                for _ in range(200):
                    if transport.input_path.exists():
                        transport.result_path.write_text("[]")
                        return
                    time.sleep(0.001)
            thread = threading.Thread(target=watcher)
            thread.start()
            try:
                with self.assertRaisesRegex(UnrealExecutionError, "Timed out"):
                    transport.submit({"commands": []})
            finally:
                thread.join(timeout=2)

    def test_submit_correlates_run_id_and_returns_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transport = FileExecutionTransport(root, timeout=1, poll_interval=0.01)

            def watcher():
                actions = root / "Content/Python/actions.json"
                while not actions.exists():
                    time.sleep(0.005)
                document = json.loads(actions.read_text())
                result = {"run_id": document["run_id"], "success": True, "commands": []}
                (root / "Content/Python/result.json").write_text(json.dumps(result))

            thread = threading.Thread(target=watcher)
            thread.start()
            result = transport.submit({"commands": []})
            thread.join()
            self.assertTrue(result["success"])

    def test_submit_times_out_when_editor_does_not_respond(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(UnrealExecutionError):
                FileExecutionTransport(directory, timeout=0.01, poll_interval=0.001).submit({"commands": []})

    def test_agent_stops_before_mutation_when_dry_run_fails(self):
        calls = []

        class Transport:
            def submit(self, document):
                calls.append(document)
                if len(calls) == 1:
                    return {"success": True, "commands": [{"data": {}}]}
                return {"success": False, "errors": [{"code": "invalid_document"}]}

        result = AgentRun(Transport(), lambda task, context: {"commands": [{"id": "read", "action": "level.inspect"}]}).run("task")
        self.assertFalse(result["success"])
        self.assertEqual(result["phase"], "plan")
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
