import json
import runpy
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/run_ue_benchmark.py"


class BenchmarkRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = runpy.run_path(str(RUNNER))

    def test_reference_manifest_dry_run_is_contract_only_not_native_coverage(self):
        manifest = self.module["load_manifest"](ROOT / "examples/benchmark-tasks.json")
        report = self.module["benchmark"](manifest)
        self.assertEqual(report["mode"], "contract")
        self.assertEqual(report["total_tasks"], 3)
        self.assertEqual(report["counts"], {"supported": 0, "contract_only": 3, "unsupported": 0, "failed": 0})
        self.assertIsNone(report["coverage_percent"])

    def test_manifest_rejects_duplicate_ids_and_shell_fields_are_ignored(self):
        manifest = {"version": "0.1", "tasks": [
            {"id": "same", "category": "supported", "shell": "rm -rf /", "document": {"commands": []}},
            {"id": "same", "category": "supported", "document": {"commands": []}},
        ]}
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate task id"):
                self.module["load_manifest"](manifest_path)

    def test_dry_run_envelope_classifies_failed_task(self):
        manifest = {"version": "0.1", "tasks": [{
            "id": "bad", "category": "supported", "document": {"commands": [
                {"id": "x", "action": "level.inspect", "arguments": {}},
                {"id": "x", "action": "level.inspect", "arguments": {}}
            ]}
        }]}
        report = self.module["benchmark"](manifest)
        self.assertEqual(report["counts"]["failed"], 1)
        self.assertEqual(report["tasks"][0]["category"], "failed")


if __name__ == "__main__":
    unittest.main()
