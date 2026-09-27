"""Validation helpers for explicit Unreal JSON values.

This module is transport-side validation only; Unreal remains authoritative for
reflection metadata and object existence.
"""

from __future__ import annotations

from typing import Any, Dict


class CodecError(ValueError):
    pass


_REFERENCE_TYPES = {"object", "class", "soft_object", "soft_class"}


def encode(value: Any) -> Any:
    """Normalize supported explicit Unreal values without guessing types."""
    if isinstance(value, dict) and "$type" in value:
        kind = value.get("$type")
        if kind in _REFERENCE_TYPES:
            path = value.get("path")
            if not isinstance(path, str) or not path:
                raise CodecError("typed Unreal reference requires a non-empty path")
            extra = set(value) - {"$type", "path"}
            if extra:
                raise CodecError("typed Unreal reference has unknown fields: {}".format(", ".join(sorted(extra))))
            return {"$type": kind, "path": path}
        if kind == "enum":
            name = value.get("name")
            if not isinstance(name, str) or not name:
                raise CodecError("typed enum requires a non-empty name")
            extra = set(value) - {"$type", "name"}
            if extra:
                raise CodecError("typed enum has unknown fields: {}".format(", ".join(sorted(extra))))
            return {"$type": "enum", "name": name}
        raise CodecError("unsupported Unreal value type: {}".format(kind))
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [encode(item) for item in value]
    if isinstance(value, dict):
        return {str(key): encode(item) for key, item in value.items()}
    raise CodecError("unsupported Unreal JSON value: {}".format(type(value).__name__))


def require_typed_reference(value: Any, kind: str) -> Dict[str, str]:
    """Require one explicit reference of the requested Unreal type."""
    if kind not in _REFERENCE_TYPES:
        raise CodecError("unsupported Unreal reference type: {}".format(kind))
    normalized = encode(value)
    if not isinstance(normalized, dict) or normalized.get("$type") != kind:
        raise CodecError("expected typed Unreal reference: {}".format(kind))
    return normalized


def require_enum(value: Any) -> Dict[str, str]:
    """Require an explicit enum value; Unreal validates the member later."""
    normalized = encode(value)
    if not isinstance(normalized, dict) or normalized.get("$type") != "enum":
        raise CodecError("expected typed enum value")
    return normalized


def require_struct(value: Any, fields: tuple[str, ...], required: tuple[str, ...] = ()) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise CodecError("struct value must be an object")
    field_set = set(fields)
    unknown = set(value) - field_set
    if unknown:
        raise CodecError("unknown struct fields: {}".format(", ".join(sorted(unknown))))
    missing = set(required) - set(value)
    if missing:
        raise CodecError("missing required struct fields: {}".format(", ".join(sorted(missing))))
    if not set(required) <= field_set:
        raise CodecError("required struct fields must be declared fields")
    return {field: encode(value[field]) for field in value}


