"""File transport for the Unreal actions.json watcher."""
from __future__ import annotations
import json
import time
import uuid
import os
import threading
import math
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Optional, Union
from .action_contract import ActionValidationError, validate_document, expand_document_recipes

class UnrealExecutionError(RuntimeError):
    """Raised when validation or file transport fails."""


@contextmanager
def commandlet_session(project_root):
    """Reserve test mailboxes without consuming a task's outstanding evidence."""
    transport = FileExecutionTransport(project_root)
    with transport.session():
        if transport.pending_path.exists():
            raise UnrealExecutionError(
                "Pending harness request exists; recover it through the CLI before running commandlet tests"
            )
        yield


def validate_response(result, document, *, correlation=True):
    """An ID match alone is not evidence that an execution completed."""
    if not isinstance(result, dict) or type(result.get("success")) is not bool or not isinstance(result.get("commands"), list):
        raise UnrealExecutionError("Invalid Unreal response envelope; execution status is unknown")
    if correlation and result.get("run_id") != document.get("run_id"):
        raise UnrealExecutionError("Unreal response correlation mismatch; execution status is unknown")
    if result.get("execution_status") == "unknown":
        raise UnrealExecutionError("Unreal execution status is unknown")
    returned = {}
    for command in result["commands"]:
        if not isinstance(command, dict) or not isinstance(command.get("id"), str) or type(command.get("success")) is not bool or command["id"] in returned:
            raise UnrealExecutionError("Invalid or duplicate Unreal command result; execution status is unknown")
        returned[command["id"]] = command
    for item in [result, *result["commands"]]:
        changed = item.get("changed_objects", [])
        if not isinstance(changed, list) or any(not isinstance(path, str) for path in changed):
            raise UnrealExecutionError("Invalid changed_objects; execution status is unknown")
    if result["success"] and not document.get("dry_run"):
        commands = expand_document_recipes(document)[0]["commands"]
        if any(c["id"] not in returned or not returned[c["id"]]["success"] for c in commands):
            raise UnrealExecutionError("Successful response omitted command results; execution status is unknown")
    return result

class FileExecutionTransport:
    def __init__(self, project_root: Union[Path, str], timeout: float = 30.0, poll_interval: float = 0.1):
        for name, value in (("timeout", timeout), ("poll_interval", poll_interval)):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(name + " must be finite and positive")
        self.project_root = Path(project_root).expanduser().resolve()
        self.input_path = self.project_root / "Content" / "Python" / "actions.json"
        self.result_path = self.project_root / "Content" / "Python" / "result.json"
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.lock_path = self.input_path.with_name("harness.lock")
        self.pending_path = self.input_path.with_name("harness.pending.json")
        self._thread_lock = threading.RLock()
        self._session_depth = 0

    @contextmanager
    def session(self):
        """Reserve the shared mailbox across planning, execution and verification."""
        if not self._thread_lock.acquire(blocking=False):
            raise UnrealExecutionError("Another harness task is using this project")
        handle = None
        locked = False
        try:
            if self._session_depth == 0:
                self.input_path.parent.mkdir(parents=True, exist_ok=True)
                handle = self.lock_path.open("a+b")
                if handle.seek(0, 2) == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                try:
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                except OSError as exc:
                    raise UnrealExecutionError("Another harness task is using this project") from exc
            self._session_depth += 1
            try:
                yield
            finally:
                self._session_depth -= 1
        finally:
            if handle is not None:
                if locked:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()
            self._thread_lock.release()

    def submit(self, document: Dict[str, Any], *, timeout: Optional[float] = None) -> Dict[str, Any]:
        with self.session():
            return self._submit(document, timeout=timeout)

    def _submit(self, document: Dict[str, Any], *, timeout: Optional[float] = None) -> Dict[str, Any]:
        if not isinstance(document, dict):
            raise TypeError("document must be an object")
        payload = dict(document)
        payload.setdefault("format_version", "1.0")
        payload.setdefault("commands", [])
        try:
            validate_document(payload)
        except ActionValidationError as exc:
            raise UnrealExecutionError(f"{exc.code}: {exc}") from exc
        payload["run_id"] = str(payload.get("run_id") or uuid.uuid4())
        duration = self.timeout if timeout is None else timeout
        if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
            raise ValueError("timeout must be finite and positive")
        self.input_path.parent.mkdir(parents=True, exist_ok=True)
        if self.pending_path.exists():
            try:
                pending = json.loads(self.pending_path.read_text(encoding="utf-8"))
                previous = json.loads(self.result_path.read_text(encoding="utf-8"))
                validate_document(pending)
                validate_response(previous, pending)
                completed = True
            except (OSError, ValueError, KeyError, TypeError, UnrealExecutionError):
                completed = False
            if not completed:
                raise UnrealExecutionError("Previous request has no matching result; execution may still be running. Check result.json before retrying. Pending request: " + str(self.pending_path))
            self._archive(pending, previous)
            self.pending_path.unlink()
        before = self._state(self.result_path)
        temporary = self.input_path.with_name(self.input_path.name + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        pending_temporary = self.pending_path.with_suffix(".tmp")
        pending_temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        pending_temporary.replace(self.pending_path)
        temporary.replace(self.input_path)
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            state = self._state(self.result_path)
            if state != before:
                try:
                    result = json.loads(self.result_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    time.sleep(min(self.poll_interval, max(0, deadline - time.monotonic())))
                    continue
                if isinstance(result, dict) and result.get("run_id") == payload["run_id"]:
                    validate_response(result, payload)
                    self._archive(payload, result)
                    self.pending_path.unlink()
                    return result
            time.sleep(min(self.poll_interval, max(0, deadline - time.monotonic())))
        raise UnrealExecutionError("Timed out waiting for Unreal result.json; execution status is unknown. Pending request retained at " + str(self.pending_path))

    def _archive(self, payload, result):
        audit_dir = self.project_root / "Saved" / "HarnessAudit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        audit_path = audit_dir / (uuid.uuid4().hex + ".json")
        result["audit_path"] = str(audit_path)
        temporary = audit_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"submitted_document": payload, "result": result}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(audit_path)

    @staticmethod
    def _state(path: Path) -> tuple[int, int]:
        try:
            stat = path.stat()
        except FileNotFoundError:
            return (0, 0)
        return (stat.st_mtime_ns, stat.st_size)
