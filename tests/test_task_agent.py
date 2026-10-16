import copy
import json
import tempfile
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import patch

from unreal_harness.acceptance import evaluate_criteria
from unreal_harness.execution import UnrealExecutionError
from unreal_harness.task_agent import TaskAgent
from unreal_harness.providers import OpenAICompatiblePlanner
from unreal_harness.action_contract import describe_contract


def command(action, arguments=None, ident="step"):
    return {"id": ident, "action": action, "arguments": arguments or {}}


def act(action, arguments=None):
    return {"status": "act", "reason": "next step", "commands": [command(action, arguments)]}


FINISH = {"status": "finish", "reason": "ready for checks", "commands": []}
RESEARCH = act("content.list", {"path": "/Game"})
CREATE = act("content.create_folder", {"path": "/Game/Target"})
CRITERIA = [{"id": "folder", "description": "Requested folder exists", "command": command("content.list", {"path": "/Game/Target"}, "check"),
             "path": "directory_exists", "operator": "equals", "value": True}]


class Planner:
    def __init__(self, steps, criteria=None):
        self.steps = deque(copy.deepcopy(steps))
        self.criteria = copy.deepcopy(CRITERIA if criteria is None else criteria)
        self.contexts = []
    def next_step(self, task, context):
        self.contexts.append(copy.deepcopy(context))
        return self.steps.popleft()
    def prepare_task(self, task, context):
        self.preparation_context = copy.deepcopy(context)
        return {"status": "ready", "goal": task, "criteria": self.criteria}


class World:
    timeout = 1
    def __init__(self, root=None):
        self.folders = {"/Game"}
        self.documents = []
        self.fail_next = False
        self.unknown_next = False
        if root:
            self.project_root = root
    def submit(self, document, **kwargs):
        self.documents.append(copy.deepcopy(document))
        if document.get("dry_run"):
            return {"success": True, "commands": []}
        results, changed = [], []
        for c in document["commands"]:
            action, args = c["action"], c["arguments"]
            data = {}
            success = True
            if action == "system.capabilities":
                data = {"actions": ["content.list", "content.create_folder"], "graph_bridge_available": False}
            elif action == "content.list":
                data = {"directory_exists": args.get("path", "/Game") in self.folders, "assets": []}
            elif action == "content.create_folder":
                if self.unknown_next:
                    raise UnrealExecutionError("Timed out")
                if self.fail_next:
                    self.fail_next, success = False, False
                else:
                    self.folders.add(args["path"])
                    changed.append(args["path"])
                data = {"path": args["path"]}
            results.append({"id": c["id"], "action": action, "success": success, "data": data,
                            **({"error": "native failure"} if not success else {})})
        return {"success": all(r["success"] for r in results), "commands": results, "changed_objects": changed}


class TaskAgentTests(unittest.TestCase):
    def test_research_precedes_frozen_criteria_and_mutation(self):
        world = World()
        planner = Planner([RESEARCH, CREATE, FINISH])
        result = TaskAgent(world, planner).run("create folder /Game/Target")
        self.assertTrue(result["success"], result)
        self.assertTrue(result["acceptance"][0]["success"])
        self.assertEqual(planner.preparation_context["history"][0]["phase"], "research")
        self.assertEqual(planner.contexts[-1]["history"][-1]["result"]["changed_objects"], ["/Game/Target"])

    def test_failed_task_criteria_trigger_another_step(self):
        world = World()
        planner = Planner([RESEARCH, act("content.create_folder", {"path": "/Game/Wrong"}), FINISH, CREATE, FINISH])
        result = TaskAgent(world, planner).run("create /Game/Target")
        self.assertTrue(result["success"], result)
        acceptances = [h for h in result["history"] if h["phase"] == "acceptance"]
        self.assertFalse(acceptances[0]["checks"][0]["success"])
        self.assertTrue(acceptances[1]["checks"][0]["success"])
        self.assertEqual(result["changed_objects"], ["/Game/Target", "/Game/Wrong"])

    def test_native_error_requires_inspection_before_revised_mutation(self):
        world = World()
        world.fail_next = True
        planner = Planner([RESEARCH, CREATE, RESEARCH, act("content.create_folder", {"path": "/Game/Target", "conflict_mode": "reuse"}), FINISH])
        result = TaskAgent(world, planner).run("create folder")
        self.assertTrue(result["success"], result)
        self.assertEqual(planner.contexts[2]["phase"], "reconcile")

    def test_mutation_during_reconciliation_is_rejected(self):
        world = World()
        world.fail_next = True
        result = TaskAgent(world, Planner([RESEARCH, CREATE, CREATE])).run("create")
        self.assertFalse(result["success"])
        self.assertIn("read-only", result["error"])

    def test_unknown_execution_status_is_never_retried(self):
        world = World()
        world.unknown_next = True
        planner = Planner([RESEARCH, CREATE, RESEARCH, CREATE])
        result = TaskAgent(world, planner).run("create")
        self.assertFalse(result["success"])
        self.assertIn("unknown", result["error"])
        self.assertEqual(len(planner.contexts), 2)

    def test_first_step_cannot_mutate(self):
        world = World()
        result = TaskAgent(world, Planner([CREATE])).run("create")
        self.assertFalse(result["success"])
        self.assertEqual(len(world.documents), 1)

    def test_model_cannot_change_criteria_after_preparation(self):
        class TamperingPlanner(Planner):
            def next_step(self, task, context):
                if context["criteria"]:
                    context["criteria"][0]["value"] = False
                return super().next_step(task, context)
        result = TaskAgent(World(), TamperingPlanner([RESEARCH, CREATE, FINISH])).run("create")
        self.assertTrue(result["success"], result)
        self.assertTrue(result["criteria"][0]["value"])

    def test_mutating_acceptance_rejected_before_changes(self):
        criteria = copy.deepcopy(CRITERIA)
        criteria[0]["command"] = CREATE["commands"][0]
        world = World()
        result = TaskAgent(world, Planner([RESEARCH, CREATE], criteria)).run("create")
        self.assertFalse(result["success"])
        self.assertEqual(world.folders, {"/Game"})

    def test_repeated_identical_mutation_is_blocked(self):
        result = TaskAgent(World(), Planner([RESEARCH, CREATE, CREATE])).run("create")
        self.assertFalse(result["success"])
        self.assertIn("identical", result["error"])

    def test_command_and_step_budgets_do_not_claim_completion(self):
        for limits in ({"max_commands": 3}, {"max_steps": 2}):
            with self.subTest(limits=limits):
                result = TaskAgent(World(), Planner([RESEARCH, CREATE, FINISH]), **limits).run("create")
                self.assertFalse(result["success"])
                self.assertIn("budget", result["error"])

    def test_task_trace_survives_failure_and_contains_original_request(self):
        with tempfile.TemporaryDirectory() as directory:
            result = TaskAgent(World(Path(directory)), Planner([RESEARCH, CREATE, CREATE])).run("original request")
            trace = json.loads(Path(result["audit_path"]).read_text(encoding="utf-8"))
            self.assertEqual(trace["task"], "original request")
            self.assertFalse(trace["report"]["success"])
            self.assertTrue(trace["events"])

    def test_trace_write_failure_preserves_changed_objects_in_response(self):
        agent = TaskAgent(World(), Planner([RESEARCH, CREATE, FINISH]))
        def persist():
            if agent.changed:
                raise OSError("disk full")
        with patch.object(agent, "_persist", side_effect=persist):
            result = agent.run("create")
        self.assertFalse(result["success"])
        self.assertEqual(result["phase"], "audit")
        self.assertEqual(result["changed_objects"], ["/Game/Target"])
        self.assertTrue(result["partial_changes"])

    def test_missing_acceptance_response_cannot_pass(self):
        self.assertFalse(evaluate_criteria(CRITERIA, [])[0]["success"])

    def test_real_provider_and_agent_loop_with_scripted_model_responses(self):
        def response(value):
            return {"choices": [{"message": {"content": json.dumps(value)}}]}
        planner = OpenAICompatiblePlanner(model="test")
        preparation = {"status": "ready", "goal": "create requested folder", "criteria": CRITERIA}
        with patch.object(planner, "_request", side_effect=[response(RESEARCH), response(preparation), response(CREATE), response(FINISH)]):
            result = TaskAgent(World(), planner).run("create folder /Game/Target")
        self.assertTrue(result["success"], result)

    def test_task_deadline_stops_after_slow_model_before_mutation(self):
        class SlowPlanner(Planner):
            def prepare_task(self, task, context):
                clock[0] += 10
                return super().prepare_task(task, context)
        clock = [100]
        world = World()
        with patch("unreal_harness.task_agent.time.monotonic", side_effect=lambda: clock[0]):
            result = TaskAgent(world, SlowPlanner([RESEARCH, CREATE]), task_timeout=5).run("create")
        self.assertFalse(result["success"])
        self.assertIn("time budget", result["error"])
        self.assertEqual(world.folders, {"/Game"})

    def test_read_only_result_contains_acceptance_observations(self):
        world = World()
        world.folders.add("/Game/Target")
        result = TaskAgent(world, Planner([RESEARCH, FINISH])).run("inspect target", read_only=True)
        self.assertTrue(result["success"])
        self.assertTrue(result["result"]["commands"][0]["data"]["directory_exists"])

    def test_unsupported_action_discovery_returns_explicit_status(self):
        planner = OpenAICompatiblePlanner(model="test")
        context = {"phase": "action", "action_catalog": describe_contract(),
                   "criteria": [], "history": [], "steps_remaining": 3, "read_only": False}
        with patch.object(planner, "_request", return_value={"choices": [{"message": {"content": '{"actions":[]}'}}]}):
            result = planner.next_step("unsupported new geometry", context)
        self.assertEqual(result["status"], "unsupported")


if __name__ == "__main__":
    unittest.main()
