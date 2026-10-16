import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from unreal_harness.cli import main, make_parser
from unreal_harness.task_agent import TaskAgent
from test_task_agent import Planner, World, RESEARCH, CREATE, FINISH


class ChatTests(unittest.TestCase):
    def chat(self, root, world, planner, prompts, *options):
        output = io.StringIO()
        with patch("unreal_harness.cli.inspect_project", return_value=(root, [])), patch("unreal_harness.cli.OpenAICompatiblePlanner", return_value=planner), patch("builtins.input", side_effect=prompts), redirect_stdout(output):
            code = main(["chat", "--project", str(root), "--model", "fixture", *options], transport_factory=lambda *a, **k: world)
        return code, output.getvalue()

    def test_chat_links_prompts_and_new_clears_context(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            world = World(root)
            planner = Planner([RESEARCH, CREATE, FINISH, RESEARCH, FINISH, RESEARCH, FINISH])
            code, output = self.chat(root, world, planner, ["create /Game/Target", "check it", "/new", "check /Game/Target", "/exit"])
            self.assertEqual(code, 0)
            self.assertIsNone(planner.contexts[0]["previous_task"])
            self.assertEqual(planner.contexts[3]["previous_task"]["task"], "create /Game/Target")
            self.assertIsNone(planner.contexts[5]["previous_task"])
            self.assertEqual(output.count("Task ID:"), 3)

    def test_chat_resume_uses_saved_criteria(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            world = World(root)
            stopped = TaskAgent(world, Planner([RESEARCH, CREATE]), max_steps=2).run("create /Game/Target")
            planner = Planner([RESEARCH, FINISH], criteria=[])
            code, output = self.chat(root, world, planner, ["/resume " + stopped["task_id"], "/tasks", "/exit"])
            self.assertEqual(code, 0)
            self.assertIn("Success:", output)
            self.assertIn(stopped["task_id"], output)
            self.assertFalse(hasattr(planner, "preparation_context"))

    def test_read_only_chat_cannot_resume_mutating_scope(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            world = World(root)
            stopped = TaskAgent(world, Planner([RESEARCH, CREATE]), max_steps=2).run("create /Game/Target")
            planner = Planner([])
            code, output = self.chat(root, world, planner, ["/resume " + stopped["task_id"], "/exit"], "--read-only")
            self.assertEqual(code, 0)
            self.assertIn("cannot resume", output)
            self.assertEqual(planner.contexts, [])

    def test_eof_closes_without_a_model_request(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            planner = Planner([])
            code, output = self.chat(root, World(root), planner, [EOFError()])
            self.assertEqual(code, 0)
            self.assertEqual(planner.contexts, [])

    def test_chat_json_mode_is_rejected_before_services(self):
        with patch("unreal_harness.cli.OpenAICompatiblePlanner") as planner, redirect_stdout(io.StringIO()) as out:
            code = main(["chat", "--project", ".", "--json"])
        self.assertEqual(code, 2)
        planner.assert_not_called()
        self.assertFalse(__import__("json").loads(out.getvalue())["success"])
