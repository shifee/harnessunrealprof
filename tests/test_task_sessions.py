import json
import tempfile
import unittest
from pathlib import Path

from test_task_agent import World, Planner, RESEARCH, CREATE, FINISH, CRITERIA, act
from unreal_harness.task_agent import TaskAgent
from unreal_harness.task_store import load_task, list_tasks
from unreal_harness.cli import make_parser, main
from unreal_harness.providers import OpenAICompatiblePlanner
from unreal_harness.action_contract import describe_contract


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.world = World(self.root)

    def stopped(self):
        return TaskAgent(self.world, Planner([RESEARCH, CREATE]), max_steps=2).run("create /Game/Target")

    def test_resume_reads_then_checks_original_criteria_without_recreating(self):
        original = self.stopped()
        original_bytes = Path(original["audit_path"]).read_bytes()
        planner = Planner([RESEARCH, FINISH], criteria=[])
        resumed = TaskAgent(self.world, planner).resume(original["task_id"])
        self.assertTrue(resumed["success"], resumed)
        self.assertEqual(resumed["criteria"], CRITERIA)
        self.assertEqual(resumed["parent_task_id"], original["task_id"])
        self.assertEqual(planner.contexts[0]["phase"], "reconcile")
        self.assertFalse(hasattr(planner, "preparation_context"))
        self.assertEqual(Path(original["audit_path"]).read_bytes(), original_bytes)
        mutations = [d for d in self.world.documents if not d.get("dry_run") and d["commands"][0]["action"] == "content.create_folder"]
        self.assertEqual(len(mutations), 1)

    def test_resume_blocks_repeated_mutation_across_attempts(self):
        original = self.stopped()
        result = TaskAgent(self.world, Planner([RESEARCH, CREATE])).resume(original["task_id"])
        self.assertFalse(result["success"])
        self.assertIn("Repeated identical mutation", result["error"])

    def test_unknown_result_blocks_before_model_or_unreal_and_creates_no_checkpoint(self):
        self.world.unknown_next = True
        original = self.stopped()
        count = len(self.world.documents)
        planner = Planner([])
        result = TaskAgent(self.world, planner).resume(original["task_id"])
        self.assertFalse(result["success"])
        self.assertIn("no confirmed result", result["error"])
        self.assertEqual(len(self.world.documents), count)
        self.assertEqual(planner.contexts, [])
        self.assertEqual(len(list_tasks(self.root)), 1)
        self.assertNotIn("audit_path", result)

    def test_late_correlated_result_permits_reconciliation_without_replay(self):
        self.world.unknown_next = True
        original = self.stopped()
        state = load_task(self.root, original["task_id"])
        request = [e["document"] for e in state["events"] if e["kind"] == "unreal_request"][-1]
        self.world.folders.add("/Game/Target")
        path = self.root / "Content/Python/result.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"run_id": request["run_id"], "success": True,
                                   "commands": [{"id": "step", "success": True}], "changed_objects": ["/Game/Target"]}))
        result = TaskAgent(self.world, Planner([RESEARCH, FINISH])).resume(original["task_id"])
        self.assertTrue(result["success"], result)
        self.assertEqual(result["changed_objects"], ["/Game/Target"])
        self.assertTrue(any(h["phase"] == "recovered" for h in result["history"]))

    def test_recovery_can_use_archive_after_mailbox_was_overwritten(self):
        self.world.unknown_next = True
        original = self.stopped()
        request = self.world.documents[-1]
        self.world.folders.add("/Game/Target")
        archive = self.root / "Saved/HarnessAudit/late.json"
        archive.parent.mkdir(parents=True)
        archive.write_text(json.dumps({"submitted_document": request, "result": {
            "run_id": request["run_id"], "success": True, "commands": [{"id": "step", "success": True}]}}))
        result = TaskAgent(self.world, Planner([RESEARCH, FINISH])).resume(original["task_id"])
        self.assertTrue(result["success"], result)

    def test_incomplete_success_response_does_not_confirm_execution(self):
        self.world.unknown_next = True
        original = self.stopped()
        request = self.world.documents[-1]
        path = self.root / "Content/Python/result.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"run_id": request["run_id"], "success": True, "commands": []}))
        result = TaskAgent(self.world, Planner([])).resume(original["task_id"])
        self.assertFalse(result["success"])
        self.assertIn("no confirmed result", result["error"])

    def test_interrupted_running_checkpoint_can_resume(self):
        original = self.stopped()
        path = Path(original["audit_path"])
        state = json.loads(path.read_text())
        state["status"], state["report"] = "running", {"success": False, "phase": "starting"}
        path.write_text(json.dumps(state))
        result = TaskAgent(self.world, Planner([RESEARCH, FINISH])).resume(original["task_id"])
        self.assertTrue(result["success"], result)

    def test_followup_gets_prior_targets_but_new_criteria(self):
        original = self.stopped()
        planner = Planner([RESEARCH, act("content.create_folder", {"path": "/Game/Other"}), FINISH],
                          criteria=[dict(CRITERIA[0], command={"id": "check", "action": "content.list", "arguments": {"path": "/Game/Other"}})])
        result = TaskAgent(self.world, planner).run("create another folder /Game/Other", session=original["task_id"])
        self.assertTrue(result["success"], result)
        self.assertEqual(planner.contexts[0]["previous_task"]["task"], "create /Game/Target")
        self.assertEqual(planner.preparation_context["previous_task"]["task_id"], original["task_id"])
        self.assertNotEqual(result["criteria"], CRITERIA)

    def test_completed_task_requires_new_followup(self):
        result = TaskAgent(self.world, Planner([RESEARCH, CREATE, FINISH])).run("create /Game/Target")
        with self.assertRaisesRegex(ValueError, "already completed"):
            TaskAgent(self.world, Planner([])).resume(result["task_id"])
        followup = TaskAgent(self.world, Planner([RESEARCH, FINISH])).run("check the folder", session=result["task_id"], read_only=True)
        self.assertTrue(followup["success"], followup)

    def test_read_only_and_explicit_verification_are_preserved(self):
        verify = {"commands": [{"id": "extra", "action": "content.list", "arguments": {"path": "/Game"}}]}
        criteria = [dict(CRITERIA[0], value=False)]
        original = TaskAgent(self.world, Planner([RESEARCH, RESEARCH], criteria), max_steps=2).run("inspect /Game/Target", read_only=True, verify=verify)
        planner = Planner([RESEARCH, FINISH])
        result = TaskAgent(self.world, planner).resume(original["task_id"])
        self.assertTrue(result["success"], result)
        self.assertTrue(planner.contexts[0]["read_only"])
        self.assertTrue(any(d["commands"][0]["id"] == "extra" for d in self.world.documents))

    def test_ids_and_project_binding_are_validated(self):
        original = self.stopped()
        with self.assertRaises(ValueError):
            load_task(self.root, "../../result")
        path = Path(original["audit_path"])
        state = json.loads(path.read_text())
        state["project"] = str(self.root / "another")
        path.write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError, "another project"):
            load_task(self.root, original["task_id"])

    def test_tasks_are_available_offline_and_read_only(self):
        original = self.stopped()
        from unittest.mock import patch
        with patch("builtins.print") as output:
            code = main(["tasks", "--project", str(self.root)], transport_factory=lambda *a, **k: self.fail("transport contacted"))
        self.assertEqual(code, 0)
        self.assertIn(original["task_id"], output.call_args[0][0])

    def test_resume_options_parse(self):
        args = make_parser().parse_args(["resume", "a" * 32, "--project", str(self.root), "--model", "local"])
        self.assertEqual(args.task_id, "a" * 32)
        with self.assertRaisesRegex(ValueError, "Unsupported task checkpoint"):
            path = self.root / "Saved/HarnessTasks" / ("a" * 32 + ".json")
            path.parent.mkdir(parents=True)
            path.write_text("{}")
            load_task(self.root, "a" * 32)

    def test_provider_includes_previous_task_in_both_requests(self):
        from unittest.mock import patch
        context = {"action_catalog": describe_contract(["content.list"]), "phase": "research", "criteria": [],
                   "history": [], "steps_remaining": 8, "read_only": True,
                   "previous_task": {"task": "inspect /Game/Target", "history": []}}
        planner = OpenAICompatiblePlanner(model="test")
        with patch.object(planner, "_structured", return_value=RESEARCH) as request:
            planner.next_step("inspect it", context)
            self.assertEqual(request.call_args.args[1]["previous_task"], context["previous_task"])
        with patch.object(planner, "_structured", return_value={}) as request:
            planner.prepare_task("inspect it", context)
            self.assertEqual(request.call_args.args[1]["previous_task"], context["previous_task"])
