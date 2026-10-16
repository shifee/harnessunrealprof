"""Explicitly configured local lifecycle hooks; argv execution without a shell."""
import copy
import fnmatch
import json
import math
import os
import subprocess
import tempfile
from pathlib import Path

from .action_contract import is_mutating

EVENTS = {"before_action", "after_action", "on_error", "on_task_complete"}


class HookError(RuntimeError):
    pass


class Hooks:
    def __init__(self, entries=(), *, cwd=None):
        self.entries = copy.deepcopy(list(entries))
        self.cwd = Path(cwd).resolve() if cwd else None
        names = set()
        for entry in self.entries:
            if not isinstance(entry, dict) or set(entry) - {"event", "name", "command", "timeout", "actions", "mutating_only"}:
                raise ValueError("Invalid hook fields")
            if not isinstance(entry.get("event"), str) or entry["event"] not in EVENTS or not isinstance(entry.get("name"), str) or not entry["name"].strip() or entry["name"] in names:
                raise ValueError("Hooks require a supported event and unique nonempty name")
            names.add(entry["name"])
            argv = entry.get("command")
            if not isinstance(argv, list) or not argv or any(not isinstance(a, str) or not a or "\0" in a for a in argv):
                raise ValueError("Hook command must be a nonempty argv array")
            timeout = entry.setdefault("timeout", 10)
            if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 60:
                raise ValueError("Hook timeout must be between 0 and 60 seconds")
            patterns = entry.get("actions", [])
            if not isinstance(patterns, list) or any(not isinstance(p, str) or not p for p in patterns):
                raise ValueError("Hook actions must be an array of patterns")
            if type(entry.get("mutating_only", False)) is not bool:
                raise ValueError("Hook mutating_only must be boolean")
            if entry["event"] in {"on_error", "on_task_complete"} and (patterns or entry.get("mutating_only")):
                raise ValueError("Action filters are supported only for action hooks")
        if len(self.entries) > 32:
            raise ValueError("At most 32 hooks are allowed")

    @classmethod
    def from_file(cls, path):
        path = Path(path).expanduser().resolve()
        config = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(config, dict) or set(config) != {"version", "hooks"} or type(config["version"]) is not int or config["version"] != 1 or not isinstance(config["hooks"], list):
            raise ValueError("Hook config must contain version: 1 and hooks: []")
        return cls(config["hooks"], cwd=path.parent)

    def emit(self, event, payload, *, record, remaining=None, best_effort=False):
        errors = []
        for hook in self.entries:
            if hook["event"] != event:
                continue
            commands = payload.get("document", {}).get("commands", [])
            eligible = [c for c in commands if not hook.get("mutating_only") or is_mutating(c)]
            patterns = hook.get("actions", [])
            if event in {"before_action", "after_action"} and (hook.get("mutating_only") or patterns):
                if not any(not patterns or any(fnmatch.fnmatchcase(c["action"], p) for p in patterns) for c in eligible):
                    continue
            item = {"kind": "hook_result", "event": event, "name": hook["name"], "success": False}
            try:
                timeout = hook["timeout"]
                if remaining is not None:
                    timeout = min(timeout, remaining())
                output = self._execute(hook["command"], {"event": event, **copy.deepcopy(payload)}, timeout)
                if event == "before_action":
                    if not isinstance(output, dict) or type(output.get("allow")) is not bool or set(output) - {"allow", "reason"} or ("reason" in output and not isinstance(output["reason"], str)):
                        raise HookError("before_action must return {allow: boolean, reason?: string}")
                    if not output["allow"]:
                        raise HookError("Blocked: " + output.get("reason", "hook denied the batch"))
                item.update(success=True, output=output)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                message = "Hook " + hook["name"] + " (" + event + "): " + str(exc)
                item["error"] = message
                errors.append(message)
            record(item)
            if errors and not best_effort:
                raise HookError(errors[-1])
        return errors

    def _execute(self, argv, payload, timeout):
        # Spool output so a verbose helper does not exhaust host memory.
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            completed = subprocess.run(argv, input=(json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"),
                                       stdout=stdout, stderr=stderr, cwd=self.cwd, timeout=timeout, shell=False,
                                       env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"),
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            stdout.seek(0)
            data = stdout.read(65537)
            stderr.seek(0)
            detail = stderr.read(4000).decode("utf-8", errors="replace")
            if completed.returncode:
                raise HookError("exit code {}: {}".format(completed.returncode, detail))
            if len(data) > 65536:
                raise HookError("stdout exceeds 64 KiB")
            return json.loads(data.decode("utf-8")) if data.strip() else None
