import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from unreal_harness import AgentRun, FileExecutionTransport, UnrealExecutionError


class AgentTransportTests(unittest.TestCase):
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

        result = AgentRun(Transport(), lambda task, context: {"commands": []}).run("task")
        self.assertFalse(result["success"])
        self.assertEqual(result["phase"], "plan")
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
