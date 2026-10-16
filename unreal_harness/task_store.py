"""Project-bound task checkpoints and conservative recovery of interrupted requests."""
import copy
import json
import re
from pathlib import Path

from .action_contract import validate_document
from .execution import validate_response, UnrealExecutionError


def project_root(project):
    root = Path(project).expanduser().resolve()
    return root.parent if root.suffix.lower() == ".uproject" else root


def load_task(project, task_id):
    if not isinstance(task_id, str) or not re.fullmatch(r"[0-9a-f]{32}", task_id):
        raise ValueError("Task ID must be 32 lowercase hexadecimal characters")
    root = project_root(project)
    path = root / "Saved/HarnessTasks" / (task_id + ".json")
    if not path.resolve().is_relative_to(root):
        raise ValueError("Task checkpoint is outside the project")
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or state.get("schema_version") != 1:
        raise ValueError("Unsupported task checkpoint; older traces cannot be resumed")
    if not isinstance(state.get("project"), str) or not state["project"]:
        raise ValueError("Checkpoint has no project identity")
    if state.get("task_id") != task_id or project_root(state["project"]) != root:
        raise ValueError("Task checkpoint belongs to another project or task")
    if not isinstance(state.get("task"), str) or not state["task"].strip():
        raise ValueError("Checkpoint has no task")
    if type(state.get("read_only")) is not bool or not isinstance(state.get("report"), dict):
        raise ValueError("Invalid checkpoint settings")
    for key in ("criteria", "history", "events"):
        if not isinstance(state.get(key), list) or any(not isinstance(item, dict) for item in state[key]):
            raise ValueError("Invalid checkpoint " + key)
    if state.get("verify") is not None:
        validate_document(state["verify"])
    return state


def recover_task(project, state):
    """Never infer completion from current object existence or replay a request."""
    state = copy.deepcopy(state)
    requests = {}
    answered = set()
    for event in state["events"]:
        if event.get("kind") == "unreal_request":
            document = event.get("document")
            validate_document(document)
            ident = document.get("run_id")
            if not isinstance(ident, str) or not ident:
                raise ValueError("Checkpoint request lacks a correlation ID")
            requests[ident] = document
        elif event.get("kind") == "unreal_response":
            ident = event.get("request_id")
            result = event.get("result")
            if ident not in requests:
                raise ValueError("Checkpoint response has no matching request")
            validate_response(result, requests[ident], correlation=False)
            answered.add(ident)
    root = project_root(project)
    for ident, document in requests.items():
        if ident in answered:
            continue
        candidates = [root / "Content/Python/result.json"]
        candidates.extend((root / "Saved/HarnessAudit").glob("*.json"))
        recovered = None
        for path in candidates:
            try:
                envelope = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(envelope, dict):
                continue
            result = envelope.get("result", envelope)
            if isinstance(result, dict) and result.get("run_id") == ident:
                try:
                    validate_response(result, document)
                except (ValueError, TypeError, UnrealExecutionError):
                    continue
                recovered = result
                break
        if recovered is None:
            raise RuntimeError("Cannot continue: request " + ident + " has no confirmed result. Check Unreal result.json; the request will not be replayed.")
        state["events"].append({"kind": "unreal_response", "phase": "recovered", "request_id": ident, "result": recovered})
        state["history"].append({"phase": "recovered", "document": document, "result": recovered})
    return state


def list_tasks(project):
    root = project_root(project)
    summaries = []
    for path in sorted((root / "Saved/HarnessTasks").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            state = load_task(root, path.stem)
            summaries.append({"task_id": state["task_id"], "task": state["task"],
                              "status": "unfinished" if state.get("status") == "running" else state.get("status", "unfinished"), "phase": state["report"].get("phase"),
                              "parent_task_id": state.get("parent_task_id")})
        except (OSError, ValueError, TypeError) as exc:
            summaries.append({"task_id": path.stem, "status": "unreadable", "error": str(exc)})
    return summaries
