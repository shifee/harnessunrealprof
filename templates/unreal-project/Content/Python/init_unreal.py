import os
import json
import runpy
import time
import traceback

import unreal


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ACTIONS_FILE = os.path.join(SCRIPT_DIR, "actions.json")
EXECUTOR_FILE = os.path.join(SCRIPT_DIR, "execute_actions.py")
PENDING_FILE = os.path.join(SCRIPT_DIR, "harness.pending.json")
RESULT_FILE = os.path.join(SCRIPT_DIR, "result.json")
CHECK_INTERVAL = 0.5
STABLE_DELAY = 1.0

_elapsed_time = 0.0
_observed_signature = None
_executed_signature = None
_pending_since = None
_is_executing = False
_watcher_handle = None


def watcher_log(message):
    unreal.log("[AI WATCHER] " + str(message))


def watcher_error(message):
    unreal.log_error("[AI WATCHER] " + str(message))


def get_file_signature():
    if not os.path.isfile(ACTIONS_FILE):
        return None
    file_stat = os.stat(ACTIONS_FILE)
    return file_stat.st_mtime_ns, file_stat.st_size


def has_pending_request():
    """Resume unacknowledged reads; never replay uncertain mutations on startup."""
    try:
        with open(PENDING_FILE, encoding="utf-8-sig") as handle:
            pending = json.load(handle)
        with open(ACTIONS_FILE, encoding="utf-8-sig") as handle:
            actions = json.load(handle)
        run_id = pending.get("run_id")
        if not run_id or actions.get("run_id") != run_id:
            return False
        contract = runpy.run_path(os.path.join(SCRIPT_DIR, "action_contract.py"))
        contract["validate_document"](actions)
        expanded = contract["expand_document_recipes"](actions)[0]
        if not actions.get("dry_run") and any(contract["is_mutating"](command) for command in expanded["commands"]):
            watcher_log("Unacknowledged mutation requires result review before retrying")
            return False
        try:
            with open(RESULT_FILE, encoding="utf-8-sig") as handle:
                result = json.load(handle)
            return not isinstance(result, dict) or result.get("run_id") != run_id
        except (OSError, ValueError):
            return True
    except (OSError, ValueError, AttributeError, TypeError, KeyError):
        return False


def execute_actions():
    global _is_executing
    if _is_executing:
        return
    if not os.path.isfile(EXECUTOR_FILE):
        watcher_error("Executor not found: " + EXECUTOR_FILE)
        return
    _is_executing = True
    try:
        watcher_log("Executing actions.json")
        runpy.run_path(EXECUTOR_FILE, run_name="__main__")
        watcher_log("Execution finished")
    except Exception as error:
        watcher_error(str(error))
        watcher_error(traceback.format_exc())
    finally:
        _is_executing = False


def watcher_tick(delta_time):
    global _elapsed_time, _observed_signature, _executed_signature, _pending_since
    _elapsed_time += delta_time
    if _elapsed_time < CHECK_INTERVAL:
        return
    _elapsed_time = 0.0
    try:
        current_signature = get_file_signature()
        if current_signature is None:
            return
        if _observed_signature is None:
            _observed_signature = current_signature
            if has_pending_request():
                _pending_since = time.monotonic()
            else:
                _executed_signature = current_signature
            return
        if current_signature != _observed_signature:
            _observed_signature = current_signature
            _pending_since = time.monotonic()
            watcher_log("actions.json change detected")
            return
        if _pending_since is None:
            return
        if time.monotonic() - _pending_since < STABLE_DELAY:
            return
        _pending_since = None
        if current_signature == _executed_signature:
            return
        _executed_signature = current_signature
        execute_actions()
    except Exception as error:
        watcher_error(str(error))
        watcher_error(traceback.format_exc())


def start_watcher():
    global _observed_signature, _executed_signature, _watcher_handle, _pending_since
    _observed_signature = get_file_signature()
    _executed_signature = _observed_signature
    if has_pending_request():
        _executed_signature = None
        _pending_since = time.monotonic()
        watcher_log("Unacknowledged CLI request scheduled")
    _watcher_handle = unreal.register_slate_post_tick_callback(watcher_tick)
    watcher_log("Watcher started")
    watcher_log("Watching: " + ACTIONS_FILE)


start_watcher()
