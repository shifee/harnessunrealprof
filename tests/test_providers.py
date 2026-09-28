import json
import unittest
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

from unreal_harness.providers import OpenAICompatiblePlanner, ProviderError


class ProviderContractTests(unittest.TestCase):
    def test_retries_with_json_schema_when_server_rejects_json_object(self):
        planner = OpenAICompatiblePlanner(model="test")
        unsupported = HTTPError(
            "http://localhost/chat/completions", 400, "Bad Request", {},
            BytesIO(b'{"error":"response_format.type must be json_schema or text"}'),
        )
        response = {"choices": [{"message": {"content": '{"commands": []}'}}]}
        with patch.object(planner, "_request", side_effect=[unsupported, response]) as request:
            self.assertEqual(planner("inspect", {"capabilities": {"actions": ["level.inspect"]}}), {"commands": []})
        self.assertIn("response_format", request.call_args_list[0].args[0])
        retry_format = request.call_args_list[1].args[0]["response_format"]
        self.assertEqual(retry_format["type"], "json_schema")
        self.assertEqual(retry_format["json_schema"]["schema"]["properties"]["commands"]["items"]["properties"]["action"]["enum"], ["level.inspect"])

    def test_valid_plan_accepts_ordered_dependencies(self):
        OpenAICompatiblePlanner.validate_plan(
            {
                "format_version": "1.0",
                "commands": [
                    {"id": "spawn", "action": "level.spawn_actor", "arguments": {}},
                    {"id": "save", "action": "project.save", "depends_on": ["spawn"], "arguments": {}},
                ],
            },
            ["level.spawn_actor", "project.save"],
        )

    def test_unknown_action_is_rejected(self):
        with self.assertRaisesRegex(ProviderError, "unsupported action"):
            OpenAICompatiblePlanner.validate_plan(
                {"commands": [{"id": "x", "action": "object.call", "arguments": {}}]},
                ["level.inspect"],
            )

    def test_forward_dependency_is_rejected(self):
        with self.assertRaisesRegex(ProviderError, "invalid dependency"):
            OpenAICompatiblePlanner.validate_plan(
                {
                    "commands": [
                        {"id": "save", "action": "project.save", "depends_on": ["spawn"], "arguments": {}},
                        {"id": "spawn", "action": "level.spawn_actor", "arguments": {}},
                    ]
                },
                ["level.spawn_actor", "project.save"],
            )

    def test_command_limit_is_rejected(self):
        with self.assertRaisesRegex(ProviderError, "200"):
            OpenAICompatiblePlanner.validate_plan(
                {"commands": [{"id": str(i), "action": "level.inspect", "arguments": {}} for i in range(201)]},
                ["level.inspect"],
            )

    def test_verification_planner_rejects_mutating_actions(self):
        planner = OpenAICompatiblePlanner(model="test")
        response = {"choices": [{"message": {"content": json.dumps({"commands": [{"id": "save", "action": "project.save", "arguments": {"save_assets": True}}], "assertions": [{"command_id": "save", "path": "$.success", "operator": "equals", "value": True}]})}}]}
        with patch.object(planner, "_request", return_value=response):
            with self.assertRaisesRegex(ProviderError, "Read-only plans"):
                planner.plan_verification("create asset", {"capabilities": {"actions": [
                    {"name": "project.save", "mutates": True}
                ]}})


if __name__ == "__main__":
    unittest.main()
