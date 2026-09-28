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
    def __init__(self, transport: FileExecutionTransport, planner: Planner):
        self.transport = transport
        self.planner = planner

    def run(self, task: str, *, verify: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        capabilities = self.transport.submit(self._batch("capabilities", "system.capabilities"))
        if not capabilities.get("success"):
            return {"success": False, "phase": "capabilities", "result": capabilities}
        data = capabilities.get("commands", [{}])[0].get("data", {})
        context = {"capabilities": data}
        plan = self.planner(task, context)
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
        # A read-only plan is complete after validation/dry-run and must not be saved or executed again.
        if not any(self._is_mutating(command, data) for command in plan["commands"]):
            return {"success": True, "phase": "plan", "plan": planned, "result": planned, "read_only": True}
        executed = self.transport.submit(plan)
        if not executed.get("success"):
            return self._failure("execute", executed, plan=planned, result=executed)
        if verify is None:
            return {"success": True, "phase": "execute", "plan": planned, "result": executed}
        try:
            verify = validate_document(dict(verify, dry_run=False))
        except (ValueError, TypeError) as exc:
            return {"success": False, "phase": "validation", "error": str(exc), "plan": planned, "result": executed}
        verified = self.transport.submit(verify)
        result = {"success": bool(verified.get("success")), "phase": "verify", "plan": planned,
                  "result": executed, "verification": verified}
        if not result["success"]:
            result.update(self._failure_details(verified))
        return result

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
