import unittest

from scripts.run_ue_smoke_test import smoke_document


class SmokeWorkflowTests(unittest.TestCase):
    def test_smoke_document_has_recovery_inspection(self):
        document = smoke_document("contract")
        commands = {command["id"]: command for command in document["commands"]}
        self.assertIn("inspect_graph", commands)
        self.assertEqual(commands["inspect_graph"]["depends_on"], ["compile"])
        self.assertEqual(commands["spawn"]["depends_on"], ["compile"])

    def test_smoke_namespace_isolated_in_game_path_and_actor_label(self):
        document = smoke_document("unique")
        serialized = str(document)
        self.assertIn("/Game/CodexHarnessSmoke/unique", serialized)
        self.assertIn("CodexHarnessSmoke_unique", serialized)


    def test_recovery_document_has_failed_and_repaired_phases(self):
        from scripts.run_ue_smoke_test import recovery_document

        commands = {command["id"]: command for command in recovery_document("contract")["commands"]}
        self.assertEqual(commands["invalid_compile"]["depends_on"], ["cast_wire"])
        self.assertEqual(commands["remove_invalid_cast"]["arguments"]["node"], "invalid_cast")
        self.assertEqual(commands["recovered_compile"]["depends_on"], ["remove_invalid_cast"])

    def test_smoke_document_uses_unique_run_id(self):
        from scripts.run_ue_smoke_test import smoke_document

        first = smoke_document("contract")
        second = smoke_document("contract")
        self.assertNotEqual(first["run_id"], second["run_id"])

    def test_smoke_result_must_match_submitted_run(self):
        from scripts.run_ue_smoke_test import validate_run_result

        with self.assertRaisesRegex(AssertionError, "run_id"):
            validate_run_result({"run_id": "stale"}, "current")

        validate_run_result({"run_id": "current"}, "current")

if __name__ == "__main__":
    unittest.main()
