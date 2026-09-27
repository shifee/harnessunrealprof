import unittest

from unreal_harness.providers import OpenAICompatiblePlanner, ProviderError


class ProviderContractTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
