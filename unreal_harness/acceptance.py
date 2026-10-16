"""Read-only, executable task criteria fixed before any mutation."""
from .action_contract import check, obj, arr, STR, ANY, is_mutating, validate_document
from .verification import equivalent

CRITERION_SCHEMA = obj({"id": STR, "description": STR, "command": {"type": "object"},
                        "path": {"type": "string"}, "operator": {"enum": ["equals", "contains", "exists", "gte", "lte"]},
                        "value": ANY}, ["id", "description", "command", "path", "operator"])
PREPARATION_SCHEMA = obj({"status": {"enum": ["ready", "unsupported"]}, "goal": STR,
                          "reason": {"type": "string"}, "criteria": arr(CRITERION_SCHEMA, 0, 20)},
                         ["status", "goal", "criteria"])


def validate_criteria(criteria, available):
    check(criteria, arr(CRITERION_SCHEMA, 1, 20), "criteria", allow_refs=False)
    seen = set()
    for criterion in criteria:
        if criterion["id"] in seen:
            raise ValueError("Duplicate criterion ID")
        seen.add(criterion["id"])
        command = criterion["command"]
        validate_document({"commands": [command]})
        if is_mutating(command) or command["action"] not in available or command.get("condition"):
            raise ValueError("Acceptance criteria require unconditional available read-only actions")
        if criterion["operator"] != "exists" and "value" not in criterion:
            raise ValueError("Criterion requires a comparison value")
        if criterion["operator"] in {"gte", "lte"} and (type(criterion["value"]) not in (int, float)):
            raise ValueError("Numeric comparison requires a number")
    return criteria


def evaluate_criteria(criteria, responses):
    checks = []
    for index, criterion in enumerate(criteria):
        response = responses[index] if index < len(responses) else {}
        command = next((c for c in response.get("commands", []) if c.get("id") == criterion["command"]["id"]), {})
        found = response.get("success") is True and command.get("success") is True and not command.get("skipped")
        actual = command.get("data")
        try:
            for part in criterion["path"].split(".") if criterion["path"] else []:
                actual = actual[int(part)] if isinstance(actual, list) else actual[part]
        except (KeyError, IndexError, TypeError, ValueError):
            found, actual = False, None
        operator, expected = criterion["operator"], criterion.get("value")
        passed = found
        if operator == "exists":
            passed = found and actual is not None
        elif operator == "equals":
            passed = found and equivalent(actual, expected)
        elif operator == "contains":
            passed = found and isinstance(actual, list) and any(equivalent(item, expected) for item in actual)
        elif operator in {"gte", "lte"}:
            passed = found and type(actual) in (int, float) and (actual >= expected if operator == "gte" else actual <= expected)
        checks.append({"id": criterion["id"], "description": criterion["description"], "success": bool(passed),
                       "actual": actual, "expected": expected, "operator": operator})
    return checks
