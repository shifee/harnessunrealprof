import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from unreal_harness.cli import build_document, main


class FakeTransport:
    instances = []

    def __init__(self, project, timeout):
        self.project = Path(project)
        self.timeout = timeout
        self.documents = []
        type(self).instances.append(self)

    def submit(self, document, timeout=None):
        self.documents.append(document)
        return {"run_id": document["run_id"], "success": True, "commands": [], "changed_objects": []}


class CliTests(unittest.TestCase):
    def setUp(self):
        FakeTransport.instances = []

    def test_spawn_actor_builds_schema_aligned_document(self):
        from unreal_harness.cli import make_parser
        args = make_parser().parse_args([
            "spawn-actor", "--project", "/tmp/Game.uproject", "--class", "/Game/BP.BP_C",
            "--location", "[1, 2, 3]", "--actor-label", "Enemy", "--save",
        ])
        document = build_document(args)
        self.assertEqual(document["commands"][0]["action"], "level.spawn_actor")
        self.assertEqual(document["commands"][0]["arguments"]["transform"]["location"], [1, 2, 3])
        self.assertEqual(document["commands"][1]["depends_on"], ["spawn_actor"])
        self.assertEqual(document["commands"][1]["action"], "project.save")

    def test_cli_uses_project_root_for_uproject_and_prints_result(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "Game.uproject"
            project.write_text("{}", encoding="utf-8")
            code = main(["capabilities", "--project", str(project)], transport_factory=FakeTransport)
        self.assertEqual(code, 0)
        self.assertEqual(FakeTransport.instances[0].project, Path(directory))
        self.assertEqual(FakeTransport.instances[0].documents[0]["commands"][0]["action"], "system.capabilities")

    def test_run_json_replaces_run_id(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "actions.json"
            path.write_text(json.dumps({"commands": [], "run_id": "stale"}), encoding="utf-8")
            code = main(["run-json", "--project", directory, str(path)], transport_factory=FakeTransport)
        self.assertEqual(code, 0)
        self.assertNotEqual(FakeTransport.instances[0].documents[0]["run_id"], "stale")

    def test_ask_runs_read_only_verification_after_mutation(self):
        from unittest.mock import patch

        class Planner:
            def __init__(self, **kwargs):
                pass

            def __call__(self, task, context):
                return {"commands": [{"id": "spawn", "action": "level.spawn_actor", "arguments": {"class": "/Script/Engine.Actor"}}]}

            def plan_verification(self, task, context):
                self.verify_context = context
                return {"commands": [{"id": "inspect", "action": "level.inspect", "arguments": {}}],
                        "assertions": [{"command_id": "inspect", "path": "$.checked", "operator": "equals", "value": True}]}

        class VerifyingTransport(FakeTransport):
            def submit(self, document, timeout=None):
                self.documents.append(document)
                index = len(self.documents)
                if index == 1:
                    return {"success": True, "commands": [
                        {"data": {"actions": ["system.capabilities", "level.spawn_actor", "level.inspect"]}},
                        {"data": {"actions": [
                            {"name": "system.capabilities", "mutating": False},
                            {"name": "level.spawn_actor", "mutating": True},
                            {"name": "level.inspect", "mutating": False},
                        ]}},
                    ]}
                if index == 4:
                    return {"success": True, "commands": [{"id": "inspect", "success": True, "data": {"checked": True}}], "changed_objects": []}
                return {"success": True, "commands": []}

        FakeTransport.instances = []
        with tempfile.TemporaryDirectory() as directory, patch("unreal_harness.cli.OpenAICompatiblePlanner", Planner):
            code = main(["ask", "--project", directory, "--model", "test", "spawn actor"], transport_factory=VerifyingTransport)
        self.assertEqual(code, 0)
        self.assertEqual(len(FakeTransport.instances[0].documents), 4)
        self.assertEqual(FakeTransport.instances[0].documents[-1]["commands"][0]["action"], "level.inspect")

    def test_invalid_vector_is_rejected_by_parser(self):
        from unreal_harness.cli import make_parser
        with self.assertRaises(SystemExit):
            make_parser().parse_args(["spawn-actor", "--project", "/tmp", "--class", "/Game/BP.BP_C", "--location", "[1, 2]"])


if __name__ == "__main__":
    unittest.main()
