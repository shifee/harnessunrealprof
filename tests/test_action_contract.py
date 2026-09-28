import json
import unittest
from pathlib import Path
from unreal_harness.action_contract import ActionValidationError, validate_document

ROOT = Path(__file__).resolve().parents[1]
class ActionContractTests(unittest.TestCase):
    def invalid(self, doc, code):
        with self.assertRaises(ActionValidationError) as caught:
            validate_document(doc)
        self.assertEqual(caught.exception.code, code)

    def test_unknown_action(self):
        self.invalid({"commands": [{"id": "x", "action": "not.real"}]}, "unsupported_action")

    def test_required_arguments_and_type(self):
        self.invalid({"commands": [{"id": "x", "action": "asset.inspect"}]}, "missing_argument")
        self.invalid({"commands": [{"id": "x", "action": "asset.inspect", "arguments": {"asset": 3}}]}, "invalid_arguments")

    def test_dependencies_and_command_limit(self):
        self.invalid({"commands": [{"id": "x", "action": "level.inspect", "depends_on": ["later"]}]}, "invalid_dependencies")
        self.invalid({"commands": [{"id": str(i), "action": "level.inspect"} for i in range(201)]}, "too_many_commands")

    def test_fixture_actions_are_host_valid(self):
        fixture = json.loads((ROOT / "tests/fixtures/actions-v0.1.json").read_text())
        document = {"format_version": "1.0", "commands": [
            {"id": "cmd_{}".format(index), "action": action, "arguments": arguments}
            for index, (action, arguments) in enumerate(fixture.items())
        ]}
        validate_document(document)

    def test_unknown_argument_is_rejected_before_transport(self):
        self.invalid({"commands": [{"id": "x", "action": "level.inspect", "arguments": {"typo": True}}]}, "invalid_arguments")

    def test_read_only_plan_is_accepted(self):
        doc = {"commands": [{"id": "read", "action": "level.inspect"}]}
        self.assertIs(validate_document(doc), doc)

if __name__ == "__main__":
    unittest.main()
