"""Minimal inspect-plan-execute-verify orchestration.

Model-specific planning is injected as a callable so this layer remains usable
without a runtime dependency on a particular provider.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from .execution import FileExecutionTransport


Planner = Callable[[str, Dict[str, Any]], Dict[str, Any]]


class AgentRun:
    """Drive one bounded Unreal task through the execution transport."""

    def __init__(self, transport: FileExecutionTransport, planner: Planner):
        self.transport = transport
        self.planner = planner

    def run(self, task: str, *, verify: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        capabilities = self.transport.submit(self._batch("capabilities", "system.capabilities"))
        if not capabilities.get("success"):
            return {"success": False, "phase": "capabilities", "result": capabilities}
        context = {"capabilities": capabilities.get("commands", [{}])[0].get("data", {})}
        plan = self.planner(task, context)
        if not isinstance(plan, dict) or not isinstance(plan.get("commands"), list):
            raise TypeError("planner must return a command document")
        dry_run = dict(plan)
        dry_run["dry_run"] = True
        planned = self.transport.submit(dry_run)
        if not planned.get("success"):
            return {"success": False, "phase": "plan", "result": planned}
        executed = self.transport.submit(plan)
        if not executed.get("success"):
            return {"success": False, "phase": "execute", "result": executed}
        if verify is None:
            return {"success": True, "phase": "execute", "plan": planned, "result": executed}
        verified = self.transport.submit(dict(verify, dry_run=False))
        return {
            "success": bool(verified.get("success")),
            "phase": "verify",
            "plan": planned,
            "result": executed,
            "verification": verified,
        }

    @staticmethod
    def _batch(command_id: str, action: str) -> Dict[str, Any]:
        return {
            "format_version": "1.0",
            "dry_run": False,
            "commands": [{"id": command_id, "action": action, "arguments": {}}],
        }
