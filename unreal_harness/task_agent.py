"""Bounded observe/act/check loop with immutable task acceptance criteria."""
import copy
import hashlib
import json
import math
import time
import uuid
from pathlib import Path
from contextlib import nullcontext

from .acceptance import PREPARATION_SCHEMA, validate_criteria, evaluate_criteria
from .action_contract import check, validate_document, describe_contract, is_mutating, expand_document_recipes
from .execution import UnrealExecutionError, validate_response
from .hooks import Hooks, HookError
from .compatibility import validate_runtime
from .task_store import load_task, recover_task, project_root


def compact(value, depth=0):
    if depth > 10:
        return "[depth truncated]"
    if isinstance(value, dict):
        return {key: compact(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [compact(item, depth + 1) for item in value[:60]] + ([{"truncated_items": len(value) - 60}] if len(value) > 60 else [])
    if isinstance(value, str) and len(value) > 4000:
        return value[:4000] + " [text truncated]"
    return value


class TaskAgent:
    def __init__(self, transport, planner, *, max_steps=8, max_commands=80, task_timeout=300, hooks=None, progress_callback=None):
        if type(max_steps) is not int or not 2 <= max_steps <= 30:
            raise ValueError("max_steps must be between 2 and 30")
        if type(max_commands) is not int or not 1 <= max_commands <= 200:
            raise ValueError("max_commands must be between 1 and 200")
        if not math.isfinite(task_timeout) or task_timeout <= 0:
            raise ValueError("task_timeout must be finite and positive")
        self.configured_hooks = hooks
        self.progress_callback = progress_callback
        self.hooks = hooks if hooks is not None else Hooks()
        self.transport, self.planner = transport, planner
        self.max_steps, self.max_commands, self.task_timeout = max_steps, max_commands, task_timeout

    def resume(self, task_id):
        root = getattr(self.transport, "project_root", None)
        if root is None:
            raise ValueError("Resume requires a project-bound transport")
        state = load_task(root, task_id)
        if state.get("status") == "completed" or state["report"].get("success"):
            raise ValueError("Task already completed; use ask --session for a new request")
        return self.run(state["task"], read_only=state["read_only"], verify=state.get("verify"), _resume=state)

    def run(self, task, *, read_only=False, verify=None, session=None, _resume=None):
        self.hooks = self.configured_hooks if self.configured_hooks is not None else Hooks()
        self.started = time.monotonic()
        self.deadline = self.started + self.task_timeout
        self.command_count = 0
        self.step_count = 0
        self.changed = set()
        self.history, self.events = [], []
        self.criteria = []
        self.last_execution = None
        self.run_id = uuid.uuid4().hex
        root = getattr(self.transport, "project_root", None)
        self.audit_path = Path(root) / "Saved/HarnessTasks" / (self.run_id + ".json") if root else None
        previous_deadline = getattr(self.planner, "task_deadline", None)
        previous_trace = getattr(self.planner, "trace_callback", None)
        attached = False
        self.task = task
        self.read_only, self.verify = read_only, copy.deepcopy(verify)
        self.parent_task_id = (_resume or {}).get("task_id") or session
        self.previous_task = None
        self.resuming = _resume is not None
        self.status = "running"
        self.persistence_ready = False
        self.report = {"success": False, "phase": "starting"}
        try:
            with getattr(self.transport, "session", nullcontext)():
                if _resume is not None or session is not None:
                    if root is None:
                        raise ValueError("Sessions require a project-bound transport")
                    saved = recover_task(root, _resume or load_task(root, session))
                    if _resume is not None:
                        if self.configured_hooks is None and saved.get("hooks") is not None:
                            settings = saved["hooks"]
                            if not isinstance(settings, dict) or set(settings) != {"entries", "cwd"} or not isinstance(settings["entries"], list) or (settings["cwd"] is not None and not isinstance(settings["cwd"], str)):
                                raise ValueError("Invalid checkpoint hook settings")
                            self.hooks = Hooks(settings["entries"], cwd=settings["cwd"])
                        self.history = copy.deepcopy(saved["history"])
                        self.events = copy.deepcopy(saved["events"])
                        self.criteria = copy.deepcopy(saved["criteria"])
                        self.previous_task = copy.deepcopy(saved.get("previous_task"))
                        for event in self.events:
                            if event.get("kind") == "unreal_response":
                                self.changed.update(event["result"].get("changed_objects", []))
                    else:
                        self.previous_task = {"task_id": saved["task_id"], "task": saved["task"],
                                              "criteria": saved["criteria"], "history": compact(saved["history"])}
                self.persistence_ready = True
                self.planner.task_deadline, self.planner.trace_callback = self.deadline, self._event
                attached = True
                self.report = self._run(task, read_only=read_only, verify=verify)
        except Exception as exc:
            self.report = {"success": False, "phase": "hooks" if isinstance(exc, HookError) else "agent", "error": str(exc), "exception_type": type(exc).__name__}
            if not self.persistence_ready:
                self.audit_path = None
        finally:
            if attached:
                self.planner.task_deadline, self.planner.trace_callback = previous_deadline, previous_trace
        self.status = "completed" if self.report.get("success") else "stopped"
        if self.parent_task_id:
            self.report["parent_task_id"] = self.parent_task_id
        self.report.update(task_id=self.run_id, steps=self.step_count, commands_submitted=self.command_count,
                           elapsed_seconds=round(time.monotonic() - self.started, 3), criteria=self.criteria,
                           history=self.history, changed_objects=sorted(self.changed), criteria_source="model")
        if self.changed:
            self.report["partial_changes"] = not self.report["success"]
        if self.last_execution:
            self.report.setdefault("result", self.last_execution)
        if self.audit_path:
            self.report["audit_path"] = str(self.audit_path)
        if self.persistence_ready:
            try:
                errors = []
                if not self.report.get("success"):
                    errors.extend(self._hooks("on_error", scope="task", report=self.report, best_effort=True))
                errors.extend(self._hooks("on_task_complete", report=self.report, best_effort=True))
                if errors:
                    self.report["hook_errors"] = errors
                    if self.report.get("success"):
                        self.report.update(success=False, phase="hooks", error=errors[0], verification_succeeded=True)
            except (OSError, RuntimeError, ValueError) as exc:
                self.report.update(success=False, phase="hooks", error=str(exc))
        self.status = "completed" if self.report.get("success") else "stopped"
        self.report["elapsed_seconds"] = round(time.monotonic() - self.started, 3)
        if self.changed:
            self.report["partial_changes"] = not self.report["success"]
        try:
            self._persist()
        except OSError as exc:
            self.report.update(success=False, phase="audit", error="Cannot persist task trace: " + str(exc))
            if self.changed:
                self.report["partial_changes"] = True
        return self.report

    def _remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("Task time budget exhausted")
        return remaining

    def _event(self, event):
        self.events.append({"elapsed_seconds": round(time.monotonic() - self.started, 3), **copy.deepcopy(event)})
        self._persist()
        if self.progress_callback:
            self.progress_callback(copy.deepcopy(event))

    def _persist(self):
        if self.audit_path and self.persistence_ready:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.audit_path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"schema_version": 1, "project": str(project_root(self.transport.project_root)),
                                            "status": self.status, "parent_task_id": self.parent_task_id,
                                            "read_only": self.read_only, "verify": self.verify,
                                            "hooks": {"entries": self.hooks.entries, "cwd": str(self.hooks.cwd) if self.hooks.cwd else None},
                                            "previous_task": self.previous_task,
                                            "task_id": self.run_id, "task": self.task, "criteria": self.criteria,
                                            "history": self.history, "events": self.events, "report": self.report},
                                           ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.audit_path)

    def _hooks(self, event, *, best_effort=False, **payload):
        return self.hooks.emit(event, {"task_id": self.run_id, "task": self.task,
                              "project": str(getattr(self.transport, "project_root", "")),
                              "read_only": self.read_only, "criteria": self.criteria,
                              "changed_objects": sorted(self.changed), **payload},
                              record=self._event, remaining=self._remaining, best_effort=best_effort)

    def _submit(self, document, phase):
        document = copy.deepcopy(document)
        document["run_id"] = uuid.uuid4().hex
        validate_document(document)
        count = len(expand_document_recipes(document)[0]["commands"])
        if self.command_count + count > self.max_commands:
            raise RuntimeError("Task command budget exhausted")
        self._hooks("before_action", document=document, phase=phase)
        remaining = self._remaining()
        self.command_count += count
        self._event({"kind": "unreal_request", "phase": phase, "document": document})
        try:
            result = self.transport.submit(document, timeout=min(getattr(self.transport, "timeout", remaining), remaining))
        except (UnrealExecutionError, OSError) as exc:
            raise RuntimeError("Execution status is unknown; task stopped without retry: " + str(exc)) from exc
        validate_response(result, document, correlation=False)
        self.changed.update(result.get("changed_objects", []))
        self._event({"kind": "unreal_response", "phase": phase, "request_id": document["run_id"], "result": result})
        if not document.get("dry_run") and (phase in {"research", "reconcile", "action"} or any(is_mutating(c) for c in document["commands"])):
            self.last_execution = copy.deepcopy(result)
        if not result["success"]:
            errors = self._hooks("on_error", scope="batch", document=document, phase=phase, result=result, best_effort=True)
            if errors:
                self._event({"kind": "hook_notification_errors", "errors": errors})
        try:
            self._hooks("after_action", document=document, phase=phase, result=result)
        except HookError:
            self.history.append({"phase": phase, "document": document, "result": result, "hook_failed": True})
            self._persist()
            raise
        return result

    @staticmethod
    def _signatures(commands):
        return [hashlib.sha256(json.dumps({"action": c["action"], "arguments": c.get("arguments", {})}, sort_keys=True).encode()).hexdigest()
                for c in commands if is_mutating(c)]

    @staticmethod
    def _normalize(document, available, read_only=False):
        validate_document(document)
        document = expand_document_recipes(document)[0]
        document.pop("recipes", None)
        if not document["commands"]:
            raise ValueError("Action step must contain commands")
        if len(document["commands"]) > 20:
            raise ValueError("A step is limited to 20 commands")
        if any(command["action"] not in available for command in document["commands"]):
            raise ValueError("Step uses unavailable actions")
        if read_only and any(is_mutating(command) for command in document["commands"]):
            raise ValueError("This phase permits only read-only commands")
        return document

    def _run(self, task, *, read_only, verify):
        capabilities = self._submit({"commands": [{"id": "capabilities", "action": "system.capabilities", "arguments": {}}]}, "capabilities")
        commands = capabilities.get("commands", [])
        if not capabilities.get("success") or not commands or not isinstance(commands[0].get("data"), dict):
            return {"success": False, "phase": "capabilities", "result": capabilities}
        data = commands[0]["data"]
        validate_runtime(data)
        actions = data.get("actions")
        if not isinstance(actions, list) or not actions or any(not isinstance(name, str) for name in actions):
            raise ValueError("Capabilities must advertise a non-empty action list")
        catalog = describe_contract(actions)
        if data.get("graph_bridge_available") is False:
            catalog = [meta for meta in catalog if not meta["name"].startswith(("graph.", "blueprint.graph."))]
        if read_only:
            catalog = [meta for meta in catalog if not meta["mutates"]]
        available = {meta["name"] for meta in catalog}
        if verify is not None:
            verify = self._normalize(verify, available, True)
        if self.criteria:
            validate_criteria(self.criteria, available)
        context = {"capabilities": data, "action_catalog": catalog, "read_only": read_only,
                   "previous_task": self.previous_task}
        research_phase = "reconcile" if self.resuming else "research"
        self._remaining()
        self.step_count = 1
        research = self.planner.next_step(task, {**copy.deepcopy(context), "phase": research_phase, "criteria": copy.deepcopy(self.criteria),
                                               "history": compact(self.history), "steps_remaining": self.max_steps})
        self._event({"kind": "agent_step", "index": 0, "step": research})
        if not isinstance(research, dict):
            raise ValueError("Invalid research response")
        if research.get("status") == "unsupported" and not research.get("commands"):
            return {"success": False, "phase": "unsupported", "error": research.get("reason", "Unsupported task")}
        if research.get("status") != "act":
            raise ValueError("The first step must inspect the project")
        document = self._normalize({"commands": research.get("commands")}, available, True)
        if all(c["action"].startswith("system.") for c in document["commands"]):
            raise ValueError("Research must inspect project state")
        preflight = self._submit(dict(document, dry_run=True), research_phase + "_preflight")
        if not preflight.get("success"):
            return {"success": False, "phase": "research", "result": preflight}
        observed = self._submit(dict(document, dry_run=False), research_phase)
        self.last_execution = observed
        self.history.append({"phase": research_phase, "document": document, "result": observed})
        if not observed.get("success"):
            return {"success": False, "phase": "research", "result": observed}
        if not self.criteria:
            preparation = self.planner.prepare_task(task, {**copy.deepcopy(context), "history": compact(self.history)})
            check(preparation, PREPARATION_SCHEMA, "preparation", allow_refs=False)
            self._event({"kind": "task_preparation", "preparation": preparation})
            if preparation["status"] == "unsupported":
                return {"success": False, "phase": "unsupported", "error": preparation.get("reason") or "Task cannot be fulfilled and checked with available tools"}
            self.criteria = copy.deepcopy(validate_criteria(preparation["criteria"], available))
            self._persist()
        phase = "action"
        mutations = set()
        for event in self.events:
            document = event.get("document", {})
            if event.get("kind") == "unreal_request" and not document.get("dry_run"):
                mutations.update(self._signatures(document.get("commands", [])))
        for index in range(1, self.max_steps):
            self.step_count = index + 1
            self._remaining()
            step_context = {**context, "phase": phase, "criteria": copy.deepcopy(self.criteria),
                            "history": compact(self.history), "steps_remaining": self.max_steps - index,
                            "commands_remaining": self.max_commands - self.command_count, "seconds_remaining": round(self._remaining(), 2)}
            step = self.planner.next_step(task, step_context)
            if not isinstance(step, dict) or step.get("status") not in {"act", "finish", "unsupported"} or not isinstance(step.get("commands"), list):
                raise ValueError("Invalid next-step response")
            self._event({"kind": "agent_step", "index": index, "step": step})
            if step["status"] != "act" and step["commands"]:
                raise ValueError("Only act steps may contain commands")
            if step["status"] == "unsupported":
                return {"success": False, "phase": "unsupported", "error": step.get("reason", "Task is unsupported")}
            if step["status"] == "finish":
                if phase in {"research", "reconcile"}:
                    raise ValueError("Research/reconciliation must execute reads before finish")
                responses = [self._submit({"commands": [criterion["command"]]}, "acceptance") for criterion in self.criteria]
                checks = evaluate_criteria(self.criteria, responses)
                entry = {"phase": "acceptance", "checks": checks, "results": responses}
                self.history.append(entry)
                self._persist()
                if all(item["success"] for item in checks):
                    if verify is not None:
                        extra = self._submit(verify, "explicit_verification")
                        if not extra.get("success") or any(c.get("skipped") or not c.get("success") for c in extra.get("commands", [])):
                            self.history.append({"phase": "explicit_verification", "result": extra})
                            continue
                    result = {"success": True, "phase": "verify", "acceptance": checks, "read_only": read_only,
                              "execution_succeeded": True, "verification": {"success": True, "results": responses}}
                    if read_only:
                        result["result"] = {"success": True, "commands": [c for response in responses for c in response.get("commands", [])]}
                    return result
                phase = "action"
                continue
            document = self._normalize({"commands": step["commands"]}, available, read_only or phase in {"research", "reconcile"})
            if phase in {"research", "reconcile"} and all(c["action"].startswith("system.") for c in document["commands"]):
                raise ValueError("Research must inspect project state, not only system capabilities")
            signatures = self._signatures(document["commands"])
            if any(signature in mutations for signature in signatures):
                raise ValueError("Repeated identical mutation blocked; inspect state and revise the step")
            # Reserve enough commands for the real submission before starting preflight.
            if self.command_count + len(document["commands"]) * 2 > self.max_commands:
                raise RuntimeError("Task command budget exhausted")
            preflight = self._submit(dict(document, dry_run=True), "preflight")
            if not preflight.get("success"):
                self.history.append({"phase": "preflight", "document": document, "result": preflight})
                self._persist()
                continue
            mutations.update(signatures)
            result = self._submit(dict(document, dry_run=False), phase)
            self.last_execution = result
            self.history.append({"phase": phase, "document": document, "result": result})
            self._persist()
            phase = "action" if result.get("success") else "reconcile"
        return {"success": False, "phase": "budget", "error": "Step budget exhausted before task acceptance passed"}
