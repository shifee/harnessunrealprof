import json
import sys
import tempfile
import unittest
from pathlib import Path

from test_task_agent import World, Planner, RESEARCH, CREATE, FINISH
from unreal_harness.hooks import Hooks, HookError
from unreal_harness.task_agent import TaskAgent
from unreal_harness.task_store import load_task


class HookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.world = World(self.root)

    def entry(self, event, code, **kwargs):
        return {"name": event, "event": event, "command": [sys.executable, "-c", code], **kwargs}

    def run_agent(self, hooks):
        return TaskAgent(self.world, Planner([RESEARCH, CREATE, FINISH]), hooks=Hooks(hooks, cwd=self.root)).run("create /Game/Target")

    def test_before_hook_blocks_without_publication_or_unknown_checkpoint_request(self):
        result = self.run_agent([self.entry("before_action", 'print(\'{"allow": false, "reason": "folder forbidden"}\')', actions=["content.create_folder"])])
        self.assertFalse(result["success"])
        self.assertEqual(result["phase"], "hooks")
        self.assertIn("folder forbidden", result["error"])
        self.assertNotIn("/Game/Target", self.world.folders)
        state = load_task(self.root, result["task_id"])
        self.assertTrue(any(e["kind"] == "hook_result" and not e["success"] for e in state["events"]))
        self.assertFalse(any(e.get("document", {}).get("commands", [{}])[0].get("action") == "content.create_folder" for e in state["events"] if e["kind"] == "unreal_request"))

    def test_allow_hook_observes_dryrun_and_real_batch(self):
        code = 'import json,sys; p=json.load(sys.stdin); open("seen.jsonl","a").write(json.dumps(p)+"\\n"); print(\'{"allow": true}\')'
        result = self.run_agent([self.entry("before_action", code, actions=["content.create_folder"], mutating_only=True)])
        self.assertTrue(result["success"], result)
        seen = [json.loads(line) for line in (self.root / "seen.jsonl").read_text().splitlines()]
        self.assertEqual([p["document"]["dry_run"] for p in seen], [True, False])
        self.assertTrue(all(p["task_id"] == result["task_id"] for p in seen))

    def test_after_hook_error_retains_confirmed_mutation_for_resume(self):
        code = 'import sys,json; p=json.load(sys.stdin); sys.exit(0 if p["document"].get("dry_run") else 7)'
        result = self.run_agent([self.entry("after_action", code, actions=["content.create_folder"])])
        self.assertFalse(result["success"])
        self.assertIn("exit code 7", result["error"])
        self.assertEqual(result["changed_objects"], ["/Game/Target"])
        self.assertTrue(result["partial_changes"])
        resumed = TaskAgent(self.world, Planner([RESEARCH, FINISH])).resume(result["task_id"])
        self.assertTrue(resumed["success"], resumed)

    def test_resume_restores_policy_unless_user_explicitly_selects_another(self):
        result = self.run_agent([self.entry("before_action", 'print(\'{"allow": false, "reason": "policy retained"}\')', actions=["content.create_folder"])])
        resumed = TaskAgent(self.world, Planner([RESEARCH, CREATE, FINISH])).resume(result["task_id"])
        self.assertFalse(resumed["success"])
        self.assertIn("policy retained", resumed["error"])
        self.assertNotIn("/Game/Target", self.world.folders)
        allowed = TaskAgent(self.world, Planner([RESEARCH, CREATE, FINISH]), hooks=Hooks()).resume(resumed["task_id"])
        self.assertTrue(allowed["success"], allowed)

    def test_terminal_hooks_get_report_and_error_handler_failure_preserves_cause(self):
        entries = [self.entry("before_action", 'print(\'{"allow": false, "reason": "blocked"}\')', actions=["content.create_folder"]),
                   self.entry("on_error", 'import sys; sys.exit(9)'),
                   self.entry("on_task_complete", 'import sys,json; p=json.load(sys.stdin); open("done.json","w").write(json.dumps(p))')]
        result = self.run_agent(entries)
        self.assertIn("blocked", result["error"])
        self.assertIn("exit code 9", result["hook_errors"][0])
        done = json.loads((self.root / "done.json").read_text())
        self.assertFalse(done["report"]["success"])
        self.assertEqual(done["event"], "on_task_complete")

    def test_known_batch_failure_notifies_and_can_still_reconcile(self):
        self.world.fail_next = True
        code = 'import json,sys; p=json.load(sys.stdin); open("errors.jsonl","a").write(json.dumps(p)+"\\n")'
        hooks = Hooks([self.entry("on_error", code)], cwd=self.root)
        planner = Planner([RESEARCH, CREATE, RESEARCH, {"status": "unsupported", "reason": "stop after reconciliation", "commands": []}])
        result = TaskAgent(self.world, planner, hooks=hooks).run("create /Game/Target")
        scopes = [json.loads(line)["scope"] for line in (self.root / "errors.jsonl").read_text().splitlines()]
        self.assertEqual(scopes, ["batch", "task"])
        self.assertEqual(planner.contexts[2]["phase"], "reconcile")
        self.assertEqual(result["phase"], "unsupported")

    def test_completion_hook_failure_does_not_claim_task_success(self):
        result = self.run_agent([self.entry("on_task_complete", 'import sys; sys.exit(3)')])
        self.assertFalse(result["success"])
        self.assertTrue(result["verification_succeeded"])
        self.assertEqual(result["phase"], "hooks")
        self.assertEqual(load_task(self.root, result["task_id"])["status"], "stopped")

    def test_timeout_is_fail_closed(self):
        result = self.run_agent([self.entry("before_action", 'import time; time.sleep(5)', timeout=0.05, actions=["content.create_folder"])])
        self.assertFalse(result["success"])
        self.assertNotIn("/Game/Target", self.world.folders)

    def test_no_shell_interpolation_and_config_relative_cwd(self):
        script = self.root / "check.py"
        script.write_text('import sys,json; assert sys.argv[1] == "$(echo secret); &"; print(\'{"allow": true}\')')
        config = self.root / "hooks.json"
        config.write_text(json.dumps({"version": 1, "hooks": [{"event": "before_action", "name": "argv", "command": [sys.executable, "check.py", "$(echo secret); &"]}]}))
        hooks = Hooks.from_file(config)
        records = []
        hooks.emit("before_action", {}, record=records.append)
        self.assertTrue(records[0]["success"])

    def test_python_hook_roundtrips_cyrillic_in_utf8(self):
        code = 'import sys,json; p=json.load(sys.stdin); print(json.dumps({"allow":True,"reason":p["task"]},ensure_ascii=False))'
        records = []
        Hooks([self.entry("before_action", code)]).emit("before_action", {"task": "Синий материал"}, record=records.append)
        self.assertEqual(records[0]["output"]["reason"], "Синий материал")

    def test_malformed_output_and_nonzero_exit_block(self):
        for code in ('print("not json")', 'print("{}")', 'print(\'{"allow": true, "commands": []}\')', 'import sys; sys.exit(2)'):
            with self.subTest(code=code), self.assertRaises(HookError):
                Hooks([self.entry("before_action", code)]).emit("before_action", {}, record=lambda e: None)

    def test_invalid_config_rejected(self):
        for entry in ({}, self.entry("bad_event", 'print("{}")'), self.entry("on_error", 'print("{}")', timeout=float("nan")),
                      self.entry("on_error", 'print("{}")', actions=["*"])):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                Hooks([entry])

    def test_large_stdout_blocks(self):
        with self.assertRaisesRegex(HookError, "64 KiB"):
            Hooks([self.entry("before_action", 'print("x" * 70000)')]).emit("before_action", {}, record=lambda e: None)

    def test_expired_budget_does_not_launch_helper(self):
        from unittest.mock import patch
        hooks = Hooks([self.entry("on_task_complete", 'print("{}")')])
        def expired():
            raise RuntimeError("budget exhausted")
        with patch.object(hooks, "_execute") as process:
            errors = hooks.emit("on_task_complete", {}, record=lambda e: None, remaining=expired, best_effort=True)
        process.assert_not_called()
        self.assertIn("budget exhausted", errors[0])

    def test_filters_match_mutating_command_not_unrelated_read(self):
        records = []
        hooks = Hooks([self.entry("before_action", 'print(\'{"allow": false}\')', actions=["content.list"], mutating_only=True)])
        hooks.emit("before_action", {"document": {"commands": CREATE["commands"] + RESEARCH["commands"]}}, record=records.append)
        self.assertEqual(records, [])
