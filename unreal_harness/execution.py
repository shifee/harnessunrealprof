"""Small transport boundary over the existing actions.json watcher.

The transport deliberately does not know Unreal action semantics. It writes one
complete document, waits for the atomic result to change, and returns the
structured envelope. Higher-level agent code can use the same boundary for
capabilities, inspection, dry-runs, and mutations.
"""

from __future__ import annotations
from typing import Any, Dict, Optional, Union
import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional


class UnrealExecutionError(RuntimeError):
    """Raised when the file transport cannot submit or receive a run."""


class FileExecutionTransport:
    """Submit serialized command documents to an open Unreal Editor project."""

    def __init__(self, project_root: Union[Path, str], timeout: float = 30.0, poll_interval: float = 0.1):
        self.project_root = Path(project_root).expanduser().resolve()
        self.input_path = self.project_root / "Content" / "Python" / "actions.json"
        self.result_path = self.project_root / "Content" / "Python" / "result.json"
        self.timeout = timeout
        self.poll_interval = poll_interval

    def submit(self, document: Dict[str, Any], *, timeout: Optional[float] = None) -> Dict[str, Any]:
        if not isinstance(document, dict):
            raise TypeError("document must be an object")
        payload = dict(document)
        payload.setdefault("format_version", "1.0")
        payload.setdefault("commands", [])
        payload["run_id"] = str(payload.get("run_id") or uuid.uuid4())
        self.input_path.parent.mkdir(parents=True, exist_ok=True)
        before = self._state(self.result_path)
        temporary = self.input_path.with_name(self.input_path.name + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.input_path)
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        while time.monotonic() < deadline:
            state = self._state(self.result_path)
            if state != before:
                try:
                    result = json.loads(self.result_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    time.sleep(self.poll_interval)
                    continue
                if result.get("run_id") == payload["run_id"]:
                    return result
            time.sleep(self.poll_interval)
        raise UnrealExecutionError("Timed out waiting for Unreal result.json")

    @staticmethod
    def _state(path: Path) -> tuple[int, int]:
        try:
            stat = path.stat()
        except FileNotFoundError:
            return (0, 0)
        return (stat.st_mtime_ns, stat.st_size)
