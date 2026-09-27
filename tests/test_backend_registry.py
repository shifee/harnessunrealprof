import unittest

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "templates/unreal-project/Content/Python"))
from backend_registry import BackendError, BackendRegistry


class BackendRegistryTests(unittest.TestCase):
    def adapter(self, **overrides):
        value = {
            "backend_id": "demo",
            "version": "1.0",
            "engine": "test",
            "capabilities": ["inspect"],
            "operations": {"inspect": lambda payload: {"echo": payload}},
        }
        value.update(overrides)
        return value

    def test_register_describe_and_invoke(self):
        registry = BackendRegistry()
        registry.register(self.adapter())
        self.assertEqual(registry.describe("demo")["capabilities"], ["inspect"])
        self.assertEqual(registry.inspect("demo", {"x": 1}), {"echo": {"x": 1}})

    def test_duplicate_registration_is_rejected(self):
        registry = BackendRegistry()
        registry.register(self.adapter())
        with self.assertRaisesRegex(BackendError, "already registered") as context:
            registry.register(self.adapter())
        self.assertEqual(context.exception.code, "backend_conflict")

    def test_disabled_backend_and_missing_operation_are_stable(self):
        registry = BackendRegistry()
        registry.register(self.adapter(enabled=False))
        with self.assertRaises(BackendError) as context:
            registry.inspect("demo", {})
        self.assertEqual(context.exception.code, "backend_disabled")
        registry = BackendRegistry()
        registry.register(self.adapter())
        with self.assertRaises(BackendError) as context:
            registry.mutate("demo", {})
        self.assertEqual(context.exception.code, "backend_dependency_missing")

    def test_capabilities_are_sorted_descriptions(self):
        registry = BackendRegistry()
        registry.register(self.adapter(backend_id="zeta"))
        registry.register(self.adapter(backend_id="alpha", capabilities=["inspect", "validate"]))
        self.assertEqual(
            registry.capabilities(),
            [
                {
                    "backend_id": "alpha",
                    "version": "1.0",
                    "engine": "test",
                    "capabilities": ["inspect", "validate"],
                    "enabled": True,
                    "dependency": None,
                },
                {
                    "backend_id": "zeta",
                    "version": "1.0",
                    "engine": "test",
                    "capabilities": ["inspect"],
                    "enabled": True,
                    "dependency": None,
                },
            ],
        )

    def test_unknown_backend_has_stable_error(self):
        registry = BackendRegistry()
        with self.assertRaises(BackendError) as context:
            registry.describe("missing")
        self.assertEqual(context.exception.code, "backend_not_found")

    def test_invoke_unknown_backend_has_stable_error(self):
        registry = BackendRegistry()
        with self.assertRaises(BackendError) as context:
            registry.invoke("missing", "inspect", {})
        self.assertEqual(context.exception.code, "backend_not_found")

    def test_operation_failure_is_wrapped_with_backend_context(self):
        registry = BackendRegistry()

        def fail(payload):
            raise ValueError("bad payload")

        registry.register(self.adapter(operations={"inspect": fail}))
        with self.assertRaises(BackendError) as context:
            registry.inspect("demo", {"bad": True})
        self.assertEqual(context.exception.code, "backend_operation_failed")
        self.assertEqual(context.exception.details, {"backend_id": "demo", "operation": "inspect"})
        self.assertEqual(str(context.exception), "bad payload")


if __name__ == "__main__":
    unittest.main()
