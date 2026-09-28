"""Bounded inspect-plan-execute-verify orchestration."""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional
from .execution import FileExecutionTransport

Planner = Callable[[str, Dict[str, Any]], Dict[str, Any]]

try:
    from .action_contract import validate_document, ActionValidationError
except ImportError:  # compatibility while contract module is unavailable
    def validate_document(document: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(document, dict) or not isinstance(document.get("commands"), list):
            raise ValueError("document must contain commands")
        return document
    class ActionValidationError(ValueError):
        pass


class AgentRun:
    """Drive one bounded Unreal task through a transport-neutral execution API."""
    HIGH_RISK_ACTIONS = {
        "object.call", "object.set", "backend.operation", "recipe.execute",
        "asset.duplicate", "project.save", "asset.save",
    }

    def __init__(self, transport: FileExecutionTransport, planner: Planner):
        self.transport = transport
        self.planner = planner

    def run(self, task: str, *, verify: Optional[Dict[str, Any]] = None,
            preview: bool = False, approve: Optional[Callable[[Dict[str, Any]], bool]] = None) -> Dict[str, Any]:
        capabilities = self.transport.submit({
            "format_version": "1.0",
            "dry_run": False,
            "commands": [
                {"id": "capabilities", "action": "system.capabilities", "arguments": {}},
                {"id": "describe_actions", "action": "system.describe_actions", "arguments": {}},
            ],
        })
        if not capabilities.get("success"):
            return {"success": False, "phase": "capabilities", "result": capabilities}
        commands = capabilities.get("commands", [])
        data = commands[0].get("data", {}) if commands else {}
        if len(commands) > 1 and isinstance(commands[1].get("data"), dict):
            data = dict(data)
            data["actions"] = commands[1]["data"].get("actions", data.get("actions", []))
        context = {"capabilities": data}
        plan = self.planner(task, context)
        if isinstance(plan, dict) and "clarification" in plan:
            clarification = plan["clarification"]
            if not isinstance(clarification, dict) or not isinstance(clarification.get("question"), str):
                return {"success": False, "phase": "clarification", "error": "malformed clarification"}
            clarification["task"] = task
            return {"success": False, "phase": "clarification", "clarification": clarification}
        try:
            plan = validate_document(plan)
        except (ValueError, TypeError) as exc:
            return {"success": False, "phase": "validation", "error": str(exc), "plan": plan}
        if not isinstance(plan, dict) or not isinstance(plan.get("commands"), list):
            return {"success": False, "phase": "validation", "error": "planner must return a command document"}
        dry_run = dict(plan); dry_run["dry_run"] = True
        planned = self.transport.submit(dry_run)
        if not planned.get("success"):
            return self._failure("plan", planned, plan=planned)
        if preview:
            return {"success": True, "phase": "preview", "preview": True, "plan": planned}
        # A read-only plan is complete after validation/dry-run and must not be saved or executed again.
        if not any(self._is_mutating(command, data) for command in plan["commands"]):
            return {"success": True, "phase": "plan", "plan": planned, "result": planned, "read_only": True}
        high_risk = [command for command in plan["commands"] if self._is_high_risk(command)]
        if high_risk:
            if approve is None:
                return {"success": False, "phase": "approval_required", "plan": planned,
                        "high_risk_commands": high_risk}
            if not approve({"plan": planned, "high_risk_commands": high_risk}):
                return {"success": False, "phase": "approval_denied", "plan": planned,
                        "high_risk_commands": high_risk}
        executed = self.transport.submit(plan)
        if not executed.get("success"):
            return self._failure("execute", executed, plan=planned, result=executed)
        if verify is None and hasattr(self.planner, "plan_verification"):
            try:
                verify = self.planner.plan_verification(task, {
                    "capabilities": data,
                    "plan": plan,
                    "execution_result": executed,
                })
            except Exception as exc:
                return {"success": False, "phase": "verify_plan", "error": str(exc),
                        "plan": planned, "result": executed}
        if verify is None:
            return {"success": True, "phase": "execute", "plan": planned, "result": executed}
        try:
            assertions = verify.get("assertions") if isinstance(verify, dict) else None
            verify_document = dict(verify, dry_run=False)
            verify_document.pop("assertions", None)
            verify = validate_document(verify_document)
            for command in verify["commands"]:
                if self._is_mutating(command, data):
                    raise ActionValidationError("Verification plan must contain read-only actions", "mutating_verification")
            if not verify["commands"]:
                raise ActionValidationError("Verification plan must contain at least one check", "empty_verification")
            if not isinstance(assertions, list) or not assertions:
                raise ActionValidationError("Verification plan must contain concrete assertions", "empty_assertions")
            command_ids = {command["id"] for command in verify["commands"]}
            for assertion in assertions:
                if (not isinstance(assertion, dict) or assertion.get("command_id") not in command_ids or
                        assertion.get("operator") not in {"equals", "not_equals", "exists"} or
                        not isinstance(assertion.get("path"), str) or not assertion["path"].startswith("$.")):
                    raise ActionValidationError("Malformed verification assertion", "invalid_assertion")
        except (ValueError, TypeError) as exc:
            return {"success": False, "phase": "verify_plan", "error": str(exc), "plan": planned, "result": executed}
        verified = self.transport.submit(verify)
        assertion_results = self._evaluate_assertions(assertions, verified)
        changed_objects = executed.get("changed_objects", [])
        result = {"success": bool(verified.get("success")) and all(item["success"] for item in assertion_results),
                  "phase": "verify", "plan": planned, "result": executed, "verification": verified,
                  "assertions": assertion_results,
                  "report": {"changed_objects": changed_objects, "verification_passed": all(item["success"] for item in assertion_results),
                             "execution_success": bool(executed.get("success"))}}
        if not result["success"]:
            result.update(self._failure_details(verified))
        return result
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
            passed = exists if operator == "exists" else exists and (value == expected if operator == "equals" else value != expected)
            results.append({"assertion": assertion, "success": passed, "actual": value if exists else None})
        return results


    @staticmethod
    def _is_mutating(command: Dict[str, Any], capabilities: Dict[str, Any]) -> bool:
        action = command.get("action")
        metadata = capabilities.get("action_metadata", capabilities.get("actions", []))

        for item in metadata if isinstance(metadata, list) else []:
            if isinstance(item, dict) and item.get("name", item.get("action")) == action:
                return bool(item.get("mutates", item.get("mutating", item.get("mutation", False))))
        try:
            from .action_contract import ACTION_METADATA
            return bool(ACTION_METADATA.get(action, {}).get("mutates", True))
        except Exception:
            return action not in {"system.capabilities", "system.describe_actions", "level.inspect", "asset.search", "object.inspect"}

    @classmethod
    def _is_high_risk(cls, command: Dict[str, Any]) -> bool:
        action = command.get("action", "")
        if action in cls.HIGH_RISK_ACTIONS:
            return True
        arguments = command.get("arguments", {})
        return isinstance(arguments, dict) and arguments.get("conflict_mode") == "update"

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
