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
                    return {"success": True, "commands": [
                        {"data": {"actions": ["system.capabilities", "level.inspect"]}},
                        {"data": {"actions": [
                            {"name": "system.capabilities", "mutating": False},
                            {"name": "level.inspect", "mutating": False},
                        ]}},
                    ]}
                return {"success": False, "errors": [{"code": "invalid_document"}]}

        result = AgentRun(Transport(), lambda task, context: {"commands": []}).run("task")
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
