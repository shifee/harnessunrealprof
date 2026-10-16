import importlib.util
import json
import tempfile
import unittest
import io
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("harness_installer", ROOT / "scripts/install.py")
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)


class InstallerTests(unittest.TestCase):
    def test_runtime_update_preserves_commands_plugins_and_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.write_project(root, "5.8")
            actions = root / "Content/Python/actions.json"
            actions.parent.mkdir(parents=True)
            actions.write_text('{"commands": []}', encoding="utf-8")
            executor = actions.with_name("execute_actions.py")
            executor.write_text("# old runtime\n", encoding="utf-8")
            original_project = project.read_bytes()
            args = INSTALLER.parse_args(["--project", str(project), "--runtime-only"])
            with redirect_stdout(io.StringIO()):
                INSTALLER.install(args)
            self.assertEqual(actions.read_text(), '{"commands": []}')
            self.assertEqual(project.read_bytes(), original_project)
            self.assertFalse((root / "Plugins").exists())
            self.assertTrue(actions.with_name("action_contract.py").exists())
            self.assertEqual(len(list(actions.parent.glob("execute_actions.py.bak.*"))), 1)
            with redirect_stdout(io.StringIO()):
                INSTALLER.install(args)
            self.assertEqual(len(list(actions.parent.glob("execute_actions.py.bak.*"))), 1)

    def test_validator_detects_executor_drift(self):
        spec = importlib.util.spec_from_file_location("harness_validator", ROOT / "scripts/validate_install.py")
        validator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(validator)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.write_project(root, "5.8")
            with redirect_stdout(io.StringIO()):
                INSTALLER.install(INSTALLER.parse_args(["--project", str(project), "--enable-plugins"]))
            self.assertFalse(validator.validate(root)[0])
            executor = root / "Content/Python/execute_actions.py"
            executor.write_text(executor.read_text(encoding="utf-8") + "\n# outdated\n", encoding="utf-8")
            self.assertTrue(any("execute_actions.py is outdated" in e for e in validator.validate(root)[0]))

    def write_project(self, root, association):
        path = root / "Test.uproject"
        path.write_text(
            json.dumps({"FileVersion": 3, "EngineAssociation": association, "Plugins": []}),
            encoding="utf-8",
        )
        return path

    def test_accepts_ue58_project(self):
        with tempfile.TemporaryDirectory(prefix="unreal-codex-install-") as directory:
            path, data = INSTALLER.validate_project_file(
                self.write_project(Path(directory), "5.8")
            )
            self.assertEqual(path.name, "Test.uproject")
            self.assertEqual(data["EngineAssociation"], "5.8")

    def test_rejects_known_older_engine(self):
        with tempfile.TemporaryDirectory(prefix="unreal-codex-install-") as directory:
            with self.assertRaisesRegex(ValueError, "targets Unreal Engine 5.8"):
                INSTALLER.validate_project_file(
                    self.write_project(Path(directory), "5.7")
                )


if __name__ == "__main__":
    unittest.main()
