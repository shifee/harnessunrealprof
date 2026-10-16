import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from unreal_harness import interactive
from unreal_harness.cli import main


class InteractiveTests(unittest.TestCase):
    def project(self, root):
        project = root / "Test.uproject"
        project.write_text('{"EngineAssociation":"5.8"}', encoding="utf-8")
        python = root / "Content/Python"
        python.mkdir(parents=True)
        (python / "execute_actions.py").write_text("# fixture")
        return project

    def test_no_arguments_open_interactive_entrypoint(self):
        with patch.object(interactive, "launch", return_value=0) as launch:
            self.assertEqual(main([]), 0)
            launch.assert_called_once()

    def test_first_setup_saves_and_discovers_model_without_starting_services(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.project(root)
            settings = root / "settings.json"
            answers = [str(project), "http://localhost:9000/v1", "1", "", "нет"]
            with patch.object(interactive, "config_path", return_value=settings), patch.object(interactive, "discover_projects", return_value=[]), patch.object(interactive, "default_editor", return_value=""), patch.object(interactive, "list_models", return_value=["fixture"]), patch("builtins.input", side_effect=answers), patch.object(interactive.subprocess, "Popen") as launch, redirect_stdout(io.StringIO()):
                configured = interactive.configure({})
                self.assertEqual(interactive.load_settings(), configured)
                self.assertEqual(configured["model"], "fixture")
                self.assertFalse(configured["auto_editor"])
                launch.assert_not_called()
            self.assertNotIn("api_key", settings.read_text(encoding="utf-8"))
            with patch.object(interactive, "config_path", return_value=settings):
                interactive.save_settings(dict(configured, api_key="private-secret"))
            self.assertNotIn("private-secret", settings.read_text(encoding="utf-8"))

    def test_prompt_context_and_new_and_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.project(root)
            calls = []
            def cli(values, **kwargs):
                calls.append(values)
                kwargs["result_callback"]({"task_id": "task" + str(len(calls)), "audit_path": "fixture", "success": True})
                return 0
            settings = {"project": str(project), "model": "fixture", "endpoint": "http://localhost:9000/v1"}
            prompts = ["синий материал", "сделай темнее", "/new", "новая задача", "/resume saved", "/exit"]
            with patch.object(interactive, "load_settings", return_value=settings), patch.object(interactive, "inspect_project", return_value=(root, [])), patch.object(interactive, "list_models", return_value=["fixture"]), patch.object(interactive, "open_editor") as editor, patch("builtins.input", side_effect=prompts), redirect_stdout(io.StringIO()):
                self.assertEqual(interactive.launch(["--no-editor"], cli_main=cli), 0)
                editor.assert_not_called()
            self.assertIn("task1", calls[1])
            self.assertNotIn("--session", calls[2])
            self.assertEqual(calls[3][:2], ["resume", "saved"])

    def test_unavailable_api_keeps_cli_open_without_sending_task(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.project(root)
            settings = {"project": str(project), "model": "fixture"}
            with patch.object(interactive, "load_settings", return_value=settings), patch.object(interactive, "inspect_project", return_value=(root, [])), patch.object(interactive, "list_models", side_effect=RuntimeError("offline")), patch("builtins.input", side_effect=["материал", "/exit"]), redirect_stdout(io.StringIO()) as output:
                calls = []
                self.assertEqual(interactive.launch(["--no-editor"], cli_main=lambda *a, **k: calls.append(a)), 0)
                self.assertEqual(calls, [])
                self.assertIn("CLI остаётся открытым", output.getvalue())

    def test_existing_editor_is_not_launched_again(self):
        with patch.object(interactive, "project_file", return_value="fixture.uproject"), patch.object(interactive, "editor_running", return_value=True), patch.object(interactive.subprocess, "Popen") as launch, redirect_stdout(io.StringIO()):
            interactive.open_editor({"project": "fixture.uproject"})
            launch.assert_not_called()

    def test_invalid_settings_can_be_replaced_with_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.project(root)
            settings = {"project": str(project), "auto_editor": False}
            with patch.object(interactive, "load_settings", side_effect=ValueError("bad settings")), patch.object(interactive, "configure", return_value=settings) as setup, patch.object(interactive, "inspect_project", return_value=(root, [])), patch("builtins.input", side_effect=["/exit"]), redirect_stdout(io.StringIO()):
                self.assertEqual(interactive.launch(["--setup"]), 0)
                setup.assert_called_once_with({})
