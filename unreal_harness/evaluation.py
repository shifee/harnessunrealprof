"""Golden checks for prompt-to-Unreal evaluations, independent of model criteria."""
import json
from pathlib import Path
from .acceptance import validate_criteria, evaluate_criteria
from .action_contract import ACTION_METADATA, validate_document


def render(value, root):
    if isinstance(value, str):
        return value.replace("{root}", root)
    if isinstance(value, dict):
        return {key: render(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [render(item, root) for item in value]
    return value


def load_suite(path):
    suite = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(suite, dict) or suite.get("version") != "1.0" or not isinstance(suite.get("cases"), list) or not suite["cases"]:
        raise ValueError("Evaluation suite requires version 1.0 and non-empty cases")
    ids = set()
    for case in suite["cases"]:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or not case["id"].replace("_", "").isalnum() or case["id"] in ids:
            raise ValueError("Case IDs must be unique alphanumeric names with optional underscores")
        ids.add(case["id"])
        if not isinstance(case.get("task"), str) or not case["task"].strip() or type(case.get("read_only", False)) is not bool:
            raise ValueError("Cases require a task string and a boolean read_only mode")
        if case.get("expected", "success") not in {"success", "unsupported"}:
            raise ValueError("Case expected must be success or unsupported")
        concrete = render(case, "/Game/HarnessAgentEvals/Validation")
        if concrete.get("setup"):
            validate_document(concrete["setup"])
        if case.get("expected", "success") == "success":
            validate_criteria(concrete.get("criteria"), set(ACTION_METADATA))
    return suite


def score(case, agent_result, responses):
    if case.get("expected", "success") == "unsupported":
        return {"success": agent_result.get("success") is False and agent_result.get("phase") == "unsupported" and not agent_result.get("changed_objects"), "checks": []}
    checks = evaluate_criteria(case["criteria"], responses)
    unchanged = not case.get("read_only") or not agent_result.get("changed_objects")
    return {"success": agent_result.get("success") is True and bool(checks) and all(check["success"] for check in checks) and unchanged,
            "checks": checks, "read_only_respected": unchanged}
