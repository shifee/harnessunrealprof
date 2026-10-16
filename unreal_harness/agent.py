"""Bounded inspect-plan-execute-verify orchestration."""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional
from contextlib import nullcontext
from .execution import FileExecutionTransport, UnrealExecutionError
from .action_contract import validate_document, describe_contract, is_mutating, expand_document_recipes
from .verification import build_verification, evaluate_verification
from .compatibility import validate_runtime

Planner = Callable[[str, Dict[str, Any]], Dict[str, Any]]



class AgentRun:
    """Drive one bounded Unreal task through a transport-neutral execution API."""
    HIGH_RISK_ACTIONS = {"object.call", "object.set", "backend.operation", "recipe.execute", "asset.duplicate", "project.save", "asset.save"}

    def __init__(self, transport: FileExecutionTransport, planner: Planner):
        self.transport = transport
        self.planner = planner

    def run(self, task: str, *, verify: Optional[Dict[str, Any]] = None, read_only: bool = False, preview=False, approve=None) -> Dict[str, Any]:
        session = getattr(self.transport, "session", nullcontext)
        with session():
            return self._run(task, verify=verify, read_only=read_only, preview=preview, approve=approve)

    def _run(self, task: str, *, verify: Optional[Dict[str, Any]] = None, read_only: bool = False, preview=False, approve=None) -> Dict[str, Any]:
        capabilities = self._submit(self._batch("capabilities", "system.capabilities"))
        if not capabilities.get("success"):
            return {"success": False, "phase": "capabilities", "result": capabilities}
        commands = capabilities.get("commands")
        if not isinstance(commands, list) or not commands or not isinstance(commands[0], dict) or not isinstance(commands[0].get("data"), dict):
            return {"success": False, "phase": "capabilities", "error": "Invalid capabilities response", "result": capabilities}
        data = commands[0]["data"]
        try:
            validate_runtime(data)
        except ValueError as exc:
            return {"success": False, "phase": "capabilities", "error": str(exc), "result": capabilities}
        actions = data.get("actions")
        if actions is not None and (not isinstance(actions, list) or any(not isinstance(action, str) and not (isinstance(action, dict) and isinstance(action.get("name"), str)) for action in actions)):
            return {"success": False, "phase": "capabilities", "error": "Invalid action catalog", "result": capabilities}
        if actions == []:
            return {"success": False, "phase": "capabilities", "error": "Unreal advertised no actions", "result": capabilities}
        catalog = describe_contract([action if isinstance(action, str) else action["name"] for action in actions] if actions is not None else None)
        if data.get("graph_bridge_available") is False:
            catalog = [meta for meta in catalog if not meta["name"].startswith(("blueprint.graph.", "graph."))]
        if read_only:
            catalog = [meta for meta in catalog if not meta["mutates"]]
        context = {"capabilities": data, "action_catalog": catalog, "read_only": read_only}
        try:
            plan = self.planner(task, context)
        except (ValueError, RuntimeError) as exc:
            return {"success": False, "phase": "planning", "error": str(exc)}
        if isinstance(plan, dict) and "clarification" in plan:
            clarification = plan["clarification"]
            if not isinstance(clarification, dict) or not isinstance(clarification.get("question"), str) or not clarification["question"].strip():
                return {"success": False, "phase": "clarification", "error": "Malformed clarification"}
            return {"success": False, "phase": "clarification", "clarification": dict(clarification, task=task)}
        try:
            plan = validate_document(plan, planner=True)
            plan = expand_document_recipes(plan)[0]
            plan.pop("recipes", None)
            if read_only and (any(is_mutating(c) for c in plan["commands"]) or plan.get("recipes")):
                raise ValueError("Read-only mode forbids mutations, recipes and save commands")
            if verify is not None:
                validate_document(verify)
                verify = expand_document_recipes(verify)[0]
                verify.pop("recipes", None)
                if not verify["commands"]:
                    raise ValueError("Explicit verification must not be empty")
                if any(is_mutating(c) for c in verify["commands"]):
                    raise ValueError("Verification must contain read-only actions only")
            available = data.get("actions")
            if isinstance(available, list) and available:
                names = {a if isinstance(a, str) else a.get("name") for a in available}
                if any(c["action"] not in names for c in plan["commands"] + (verify["commands"] if verify else [])):
                    raise ValueError("Plan uses actions unavailable in the running Unreal harness")
            if data.get("graph_bridge_available") is False and any(c["action"].startswith(("blueprint.graph.", "graph.")) for c in plan["commands"]):
                raise ValueError("Graph bridge is unavailable in the running Unreal Editor")
        except (ValueError, TypeError) as exc:
            return {"success": False, "phase": "validation", "error": str(exc), "plan": plan}
        if not isinstance(plan, dict) or not isinstance(plan.get("commands"), list):
            return {"success": False, "phase": "validation", "error": "planner must return a command document"}
        dry_run = dict(plan); dry_run["dry_run"] = True
        planned = self._submit(dry_run)
        if not planned.get("success"):
            return self._failure("plan", planned, plan=planned)
        if preview:
            return {"success": True, "phase": "preview", "preview": True, "plan": planned, "document": plan}
        high_risk = [command for command in plan["commands"] if is_mutating(command) and self._is_high_risk(command)]
        if high_risk and (approve is None or not approve({"plan": planned, "high_risk_commands": high_risk})):
            return {"success": False, "phase": "approval_required" if approve is None else "approval_denied",
                    "plan": planned, "high_risk_commands": high_risk}
        # dry_run validates only; even a read-only task must actually execute.
        read_only = not any(is_mutating(c) for c in plan["commands"]) and not plan.get("recipes")
        executed = self._submit(dict(plan, dry_run=False))
        if not executed.get("success"):
            return self._failure("execute", executed, plan=planned, result=executed)
        if read_only and verify is None:
            return {"success": True, "phase": "execute", "plan": planned, "result": executed, "read_only": True}
        assertions = []
        automatic = build_verification(plan, executed) if verify is None else None
        generated_verification = False
        if verify is None and automatic[2] and hasattr(self.planner, "plan_verification"):
            try:
                generated = self.planner.plan_verification(task, {"capabilities": data, "action_catalog": catalog,
                                                                 "plan": plan, "execution_result": executed})
                assertions = generated.get("assertions", [])
                verify = dict(generated)
                verify.pop("assertions", None)
                self._validate_assertions(assertions, verify)
                validate_document(verify)
                verify = expand_document_recipes(verify)[0]
                verify.pop("recipes", None)
                if any(is_mutating(command) for command in verify["commands"]):
                    raise ValueError("Verification must contain read-only actions")
                if actions is not None:
                    available_names = {a if isinstance(a, str) else a["name"] for a in actions}
                    if any(c["action"] not in available_names for c in verify["commands"]):
                        raise ValueError("Verification uses unavailable actions")
                generated_verification = True
            except (ValueError, TypeError, AttributeError, RuntimeError) as exc:
                return {"success": False, "phase": "verify_plan", "error": str(exc), "plan": planned,
                        "result": executed, "execution_succeeded": True}
        if verify is None:
            verify, checks, uncovered = automatic
        else:
            checks, uncovered = [], []
        if generated_verification:
            used = {command["id"] for command in verify["commands"]}
            for command, check in zip(automatic[0]["commands"], automatic[1]):
                identifier = "host_" + command["id"]
                while identifier in used:
                    identifier = "host_" + identifier
                used.add(identifier)
                verify["commands"].append(dict(command, id=identifier))
                checks.append(dict(check, id=identifier))
            validate_document(verify)
        verified = self._submit(dict(verify, dry_run=False)) if verify["commands"] else {"success": True, "commands": []}
        errors = evaluate_verification(verified, checks) + uncovered
        assertion_results = self._evaluate_assertions(assertions, verified)
        errors += ["Postcondition assertion failed: " + item["assertion"]["path"] for item in assertion_results if not item["success"]]
        if any(command.get("skipped") for command in verified.get("commands", [])):
            errors.append("Read-back verification commands were skipped")
        result = {"success": bool(verified.get("success")) and not errors, "phase": "verify",
                  "plan": planned, "result": executed, "verification": verified,
                  "verification_errors": errors, "execution_succeeded": True}
        if assertions:
            result["assertions"] = assertion_results
        if not result["success"]:
            result.update(self._failure_details(verified))
            result["error"] = "; ".join(errors) or "Read-back verification failed"
        return result

    @staticmethod
    def _validate_assertions(assertions, document):
        if not isinstance(assertions, list) or not assertions or not document.get("commands"):
            raise ValueError("Verification needs commands and concrete assertions")
        ids = {command["id"] for command in document["commands"]}
        for assertion in assertions:
            if (not isinstance(assertion, dict) or assertion.get("command_id") not in ids or
                    assertion.get("operator") not in {"equals", "not_equals", "exists"} or
                    not isinstance(assertion.get("path"), str) or not assertion["path"].startswith("$.") or
                    (assertion["operator"] != "exists" and "value" not in assertion)):
                raise ValueError("Malformed verification assertion")

    @staticmethod
    def _evaluate_assertions(assertions: list[Dict[str, Any]], verified: Dict[str, Any]) -> list[Dict[str, Any]]:
        import re
        results = []
        command_results = {item.get("id"): item for item in verified.get("commands", []) if isinstance(item, dict)}
        for assertion in assertions:
            value: Any = command_results.get(assertion["command_id"], {}).get("data")
            path = assertion["path"][2:]
            tokens = re.findall(r"(?:^|\.)([A-Za-z_][A-Za-z0-9_]*)|\[([0-9]+)\]", path)
            if not tokens or "".join(("." if index and field else "") + (field or "[" + item + "]")
                                      for index, (field, item) in enumerate(tokens)) != path:
                results.append({"assertion": assertion, "success": False, "actual": None})
                continue
            exists = True
            for field, index in tokens:
                try:
                    value = value[field] if field else value[int(index)]
                except (KeyError, IndexError, TypeError):
                    exists = False
                    break
            operator = assertion["operator"]
            expected = assertion.get("value")
            passed = (exists if operator == "exists" else exists and (value == expected if operator == "equals" else value != expected))
            passed = passed and command_results.get(assertion["command_id"], {}).get("success") is True
            results.append({"assertion": assertion, "success": passed, "actual": value if exists else None})
        return results

    @classmethod
    def _is_high_risk(cls, command: Dict[str, Any]) -> bool:
        action = command.get("action", "")
        if action in cls.HIGH_RISK_ACTIONS:
            return True
        arguments = command.get("arguments", {})
        return isinstance(arguments, dict) and arguments.get("conflict_mode") == "update"


    def _submit(self, document):
        try:
            return self.transport.submit(document)
        except (UnrealExecutionError, OSError) as exc:
            return {"success": False, "error": str(exc), "errors": [{"code": "transport_error", "message": str(exc)}],
                    "execution_status": "unknown" if not document.get("dry_run") else "not_executed"}

    @staticmethod
    def _failure(phase: str, result: Dict[str, Any], **extra: Any) -> Dict[str, Any]:
        response = {"success": False, "phase": phase, "result": result}
        response.update(extra); response.update(AgentRun._failure_details(result))
        return response

    @staticmethod
    def _failure_details(result: Dict[str, Any]) -> Dict[str, Any]:
        details: Dict[str, Any] = {}
        for key in ("failed_command", "failed_action", "partial_changes", "changed_objects", "audit_path"):
            if key in result:
                details[key] = result[key]
        commands = result.get("commands")
        if isinstance(commands, list):
            failed = next((item for item in commands if isinstance(item, dict) and not item.get("success", True)), None)
            if failed:
                details.setdefault("failed_command", failed.get("id"))
                details.setdefault("failed_action", failed.get("action"))
                changed = failed.get("changed_objects") or []
                if changed:
                    details.setdefault("partial_changes", True)
                    details.setdefault("changed_objects", changed)
        if "failed_command" not in details and isinstance(result.get("errors"), list) and result["errors"]:
            error = result["errors"][0]
            if isinstance(error, dict) and "command_id" in error:
                details["failed_command"] = error["command_id"]
        if "changed_objects" in result and result.get("changed_objects"):
            details.setdefault("partial_changes", True)
        return details

    @staticmethod
    def _batch(command_id: str, action: str) -> Dict[str, Any]:
        return {"format_version": "1.0", "dry_run": False,
                "commands": [{"id": command_id, "action": action, "arguments": {}}]}
