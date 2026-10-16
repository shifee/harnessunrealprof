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
    def __init__(self, transport: FileExecutionTransport, planner: Planner):
        self.transport = transport
        self.planner = planner

    def run(self, task: str, *, verify: Optional[Dict[str, Any]] = None, read_only: bool = False) -> Dict[str, Any]:
        session = getattr(self.transport, "session", nullcontext)
        with session():
            return self._run(task, verify=verify, read_only=read_only)

    def _run(self, task: str, *, verify: Optional[Dict[str, Any]] = None, read_only: bool = False) -> Dict[str, Any]:
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
        # dry_run validates only; even a read-only task must actually execute.
        read_only = not any(is_mutating(c) for c in plan["commands"]) and not plan.get("recipes")
        executed = self._submit(dict(plan, dry_run=False))
        if not executed.get("success"):
            return self._failure("execute", executed, plan=planned, result=executed)
        if read_only and verify is None:
            return {"success": True, "phase": "execute", "plan": planned, "result": executed, "read_only": True}
        if verify is None:
            verify, checks, uncovered = build_verification(plan, executed)
        else:
            checks, uncovered = [], []
        verified = self._submit(dict(verify, dry_run=False)) if verify["commands"] else {"success": True, "commands": []}
        errors = evaluate_verification(verified, checks) + uncovered
        if any(command.get("skipped") for command in verified.get("commands", [])):
            errors.append("Read-back verification commands were skipped")
        result = {"success": bool(verified.get("success")) and not errors, "phase": "verify",
                  "plan": planned, "result": executed, "verification": verified,
                  "verification_errors": errors, "execution_succeeded": True}
        if not result["success"]:
            result.update(self._failure_details(verified))
            result["error"] = "; ".join(errors) or "Read-back verification failed"
        return result

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
