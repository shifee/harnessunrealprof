import json
import tempfile
import unittest
from pathlib import Path

from unreal_harness.evaluation import load_suite, render, score

ROOT = Path(__file__).resolve().parents[1]


class AgentEvaluationTests(unittest.TestCase):
    def test_shipped_suite_contains_real_prompts_and_golden_checks(self):
        suite = load_suite(ROOT / "evals/agent-tasks.json")
        self.assertEqual(len(suite["cases"]), 5)
        self.assertTrue(any(c.get("read_only") for c in suite["cases"]))
        self.assertTrue(any(c.get("expected") == "unsupported" for c in suite["cases"]))

    def test_model_success_does_not_override_failed_golden_check(self):
        case = render(load_suite(ROOT / "evals/agent-tasks.json")["cases"][0], "/Game/Eval")
        response = {"success": True, "commands": [{"id": "folder", "success": True, "data": {"directory_exists": False}}]}
        grade = score(case, {"success": True, "acceptance": [{"success": True}]}, [response])
        self.assertFalse(grade["success"])

    def test_unsupported_task_must_not_leave_changes(self):
        case = {"expected": "unsupported"}
        self.assertTrue(score(case, {"success": False, "phase": "unsupported"}, [])["success"])
        self.assertFalse(score(case, {"success": False, "phase": "unsupported", "changed_objects": ["/Game/Unexpected"]}, [])["success"])

    def test_case_namespace_is_rendered_in_tasks_and_checks(self):
        case = load_suite(ROOT / "evals/agent-tasks.json")["cases"][1]
        concrete = render(case, "/Game/IsolatedRun")
        self.assertNotIn("{root}", json.dumps(concrete))
        self.assertIn("/Game/IsolatedRun", concrete["task"])

    def test_invalid_golden_mutation_is_rejected(self):
        suite = load_suite(ROOT / "evals/agent-tasks.json")
        suite["cases"][0]["criteria"][0]["command"] = {"id": "bad", "action": "content.create_folder", "arguments": {"path": "/Game/Bad"}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "suite.json"
            path.write_text(json.dumps(suite), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "read-only"):
                load_suite(path)
