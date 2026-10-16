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
                    return {"success": True, "commands": [
                        {"data": {"actions": ["system.capabilities", "level.inspect"]}},
                        {"data": {"actions": [
                            {"name": "system.capabilities", "mutating": False},
                            {"name": "level.inspect", "mutating": False},
                        ]}},
                    ]}
                return {"success": False, "errors": [{"code": "invalid_document"}]}

        result = AgentRun(Transport(), lambda task, context: {"commands": [{"id": "read", "action": "level.inspect"}]}).run("task")
        self.assertFalse(result["success"])
        self.assertEqual(result["phase"], "plan")
        self.assertEqual(len(calls), 2)

    def test_mutation_automatically_runs_read_only_verification(self):
        calls = []

        class Transport:
            def submit(self, document):
                calls.append(document)
                if len(calls) == 1:
                    return {"success": True, "commands": [
                        {"data": {"actions": ["system.capabilities", "level.spawn_actor", "level.inspect"]}},
                        {"data": {"actions": [
                            {"name": "system.capabilities", "mutating": False},
                            {"name": "level.spawn_actor", "mutating": True},
                            {"name": "level.inspect", "mutating": False},
                        ]}},
                    ]}
                if len(calls) == 4:
                    return {"success": True, "commands": [{"id": "inspect", "success": True, "data": {"actors": [{"label": "Enemy"}]}}], "changed_objects": []}
                return {"success": True, "commands": []}

        class Planner:
            def __call__(self, task, context):
                return {"commands": [{"id": "spawn", "action": "level.spawn_actor", "arguments": {"class": "/Script/Engine.Actor"}}]}

            def plan_verification(self, task, context):
                self.context = context
                return {"commands": [{"id": "inspect", "action": "level.inspect", "arguments": {}}],
                        "assertions": [{"command_id": "inspect", "path": "$.actors[0].label", "operator": "equals", "value": "Enemy"}]}

        planner = Planner()
        result = AgentRun(Transport(), planner).run("spawn actor")
        self.assertTrue(result["success"])
        self.assertEqual(result["phase"], "verify")
        self.assertEqual(len(calls), 4)
        self.assertTrue(calls[-1]["commands"][0]["action"], "level.inspect")
        self.assertIn("execution_result", planner.context)

    def test_verification_plan_failure_is_not_reported_as_success(self):
        class Transport:
            def __init__(self):
                self.calls = 0

            def submit(self, document):
                self.calls += 1
                actions = [
                    {"name": "system.capabilities", "mutates": False},
                    {"name": "level.spawn_actor", "mutates": True},
                ]
                return {"success": True, "commands": [{"data": {"actions": actions}}]}

        class Planner:
            def __call__(self, task, context):
                return {"commands": [{"id": "spawn", "action": "level.spawn_actor", "arguments": {"class": "/Script/Engine.Actor"}}]}

            def plan_verification(self, task, context):
                raise RuntimeError("no read-only check available")

        result = AgentRun(Transport(), Planner()).run("spawn actor")
        self.assertFalse(result["success"])
        self.assertEqual(result["phase"], "verify_plan")
        self.assertIn("result", result)
    def test_agent_rejects_mutating_verification_batch(self):
        calls = []

        class Transport:
            def submit(self, document):
                calls.append(document)
                if len(calls) == 1:
                    return {"success": True, "commands": [
                        {"data": {"actions": ["level.spawn_actor", "project.save"]}},
                        {"data": {"actions": [
                            {"name": "level.spawn_actor", "mutating": True},
                            {"name": "project.save", "mutating": True},
                        ]}},
                    ]}
                return {"success": True, "commands": []}

        class Planner:
            def __call__(self, task, context):
                return {"commands": [{"id": "spawn", "action": "level.spawn_actor", "arguments": {"class": "/Script/Engine.Actor"}}]}

            def plan_verification(self, task, context):
                return {"commands": [{"id": "save_again", "action": "project.save", "arguments": {"save_assets": True}}],
                        "assertions": [{"command_id": "save_again", "path": "$.success", "operator": "equals", "value": True}]}
        result = AgentRun(Transport(), Planner()).run("spawn actor")
        self.assertFalse(result["success"])
        self.assertEqual(result["phase"], "verify_plan")
        self.assertEqual(len(calls), 3)



    def test_preview_does_not_execute_mutation(self):
        calls = []

        class Transport:
            def submit(self, document):
                calls.append(document)
                if len(calls) == 1:
                    return {"success": True, "commands": [{"data": {}}]}
                return {"success": True, "commands": []}

        plan = {"commands": [{"id": "spawn", "action": "level.spawn_actor", "arguments": {"class": "/Script/Engine.Actor"}}]}
        result = AgentRun(Transport(), lambda task, context: plan).run("spawn", preview=True)
        self.assertTrue(result["success"])
        self.assertEqual(result["phase"], "preview")
        self.assertEqual(len(calls), 2)
    def test_denied_high_risk_action_is_not_executed(self):
        calls = []

        class Transport:
            def submit(self, document):
                calls.append(document)
                if len(calls) == 1:
                    return {"success": True, "commands": [{"data": {}}]}
                return {"success": True, "commands": []}

        plan = {"commands": [{"id": "op", "action": "backend.operation", "arguments": {"backend": "example", "operation": "mutate", "payload": {}}}]}
        result = AgentRun(Transport(), lambda task, context: plan).run("call", approve=lambda summary: False)
        self.assertEqual(result["phase"], "approval_denied")
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[-1]["dry_run"])

    def test_failed_postcondition_returns_actual_value(self):
        calls = []

        class Transport:
            def submit(self, document):
                calls.append(document)
                if len(calls) == 1:
                    return {"success": True, "commands": [{"data": {"actions": [
                        {"name": "level.spawn_actor", "mutating": True},
                        {"name": "level.inspect", "mutating": False},
                    ]}}]}
                if len(calls) == 4:
                    return {"success": True, "commands": [{"id": "inspect", "success": True, "data": {"label": "Actual"}}]}
                return {"success": True, "commands": []}

        class Planner:
            def __call__(self, task, context):
                return {"commands": [{"id": "spawn", "action": "level.spawn_actor", "arguments": {"class": "/Script/Engine.Actor"}}]}

            def plan_verification(self, task, context):
                return {"commands": [{"id": "inspect", "action": "level.inspect", "arguments": {}}],
                        "assertions": [{"command_id": "inspect", "path": "$.label", "operator": "equals", "value": "Expected"}]}

        result = AgentRun(Transport(), Planner()).run("spawn", approve=lambda summary: True)
        self.assertFalse(result["success"])
        self.assertEqual(result["assertions"][0]["actual"], "Actual")

if __name__ == "__main__":
    unittest.main()
