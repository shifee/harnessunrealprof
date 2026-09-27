import unittest

from unreal_harness.codec import (
    CodecError,
    encode,
    require_enum,
    require_struct,
    require_typed_reference,
)


class CodecTests(unittest.TestCase):
    def test_nested_typed_values_are_normalized_without_guessing(self):
        value = encode({"asset": {"$type": "object", "path": "/Game/M.M"}, "enabled": True})
        self.assertEqual(value["asset"], {"$type": "object", "path": "/Game/M.M"})
        self.assertIs(value["enabled"], True)

    def test_typed_values_reject_extra_fields(self):
        with self.assertRaisesRegex(CodecError, "unknown fields"):
            encode({"$type": "object", "path": "/Game/M.M", "class": "Material"})

    def test_reference_and_enum_require_explicit_types(self):
        self.assertEqual(
            require_typed_reference({"$type": "class", "path": "/Script/Engine.Actor"}, "class")["path"],
            "/Script/Engine.Actor",
        )
        self.assertEqual(require_enum({"$type": "enum", "name": "Visible"})["name"], "Visible")
        with self.assertRaisesRegex(CodecError, "expected typed"):
            require_enum("Visible")

    def test_all_typed_reference_categories_round_trip(self):
        value = encode({
            "hard_object": {"$type": "object", "path": "/Game/M.M"},
            "hard_class": {"$type": "class", "path": "/Script/Engine.Actor"},
            "soft_object": {"$type": "soft_object", "path": "/Game/M.M"},
            "soft_class": {"$type": "soft_class", "path": "/Script/Engine.Actor"},
            "enum": {"$type": "enum", "name": "Visible"},
            "values": [{"$type": "enum", "name": "Hidden"}],
        })
        self.assertEqual(value["soft_class"]["path"], "/Script/Engine.Actor")
        self.assertEqual(value["values"][0], {"$type": "enum", "name": "Hidden"})

    def test_struct_round_trip_preserves_nested_typed_values(self):
        value = require_struct(
            {"asset": {"$type": "object", "path": "/Game/M.M"}, "enabled": True},
            ("asset", "enabled"),
            ("asset",),
        )
        self.assertEqual(value["asset"]["$type"], "object")
        self.assertIs(value["enabled"], True)

    def test_invalid_reference_and_enum_shapes_fail(self):
        with self.assertRaisesRegex(CodecError, "non-empty path"):
            encode({"$type": "soft_object", "path": ""})
        with self.assertRaisesRegex(CodecError, "non-empty name"):
            encode({"$type": "enum", "name": ""})

    def test_struct_validates_unknown_and_required_fields(self):
        self.assertEqual(require_struct({"x": 1}, ("x", "y"), ("x",)), {"x": 1})
        with self.assertRaisesRegex(CodecError, "unknown struct fields"):
            require_struct({"z": 1}, ("x",))
        with self.assertRaisesRegex(CodecError, "missing required"):
            require_struct({}, ("x",), ("x",))


if __name__ == "__main__":
    unittest.main()
