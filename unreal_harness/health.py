"""Actionable project and connection diagnostics without asset mutations."""
import ast
import json
import os
from pathlib import Path

from .execution import FileExecutionTransport, UnrealExecutionError, validate_response
from .providers import ProviderError, list_models
from .compatibility import validate_runtime


def inspect_project(project):
    requested = Path(project).expanduser().resolve()
    root = requested.parent if requested.suffix.lower() == ".uproject" else requested
    checks = []
    def record(name, status, message, fix=None):
        check = {"name": name, "status": status, "message": message}
        if fix:
            check["fix"] = fix
        checks.append(check)

    projects = [requested] if requested.suffix.lower() == ".uproject" else list(root.glob("*.uproject"))
    if len(projects) != 1 or not projects[0].is_file():
        record("project", "fail", "Expected one existing .uproject", "Pass --project with the exact .uproject path")
        return root, checks
    try:
        data = json.loads(projects[0].read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("project JSON must be an object")
        record("project", "pass", str(projects[0]))
    except (OSError, ValueError) as exc:
        record("project", "fail", str(exc), "Repair the .uproject JSON")
        return root, checks
    association = str(data.get("EngineAssociation", ""))
    if association[:1].isdigit() and association.split(".")[:2] != ["5", "8"]:
        record("engine", "fail", "Expected Unreal 5.8; found " + association, "Use an Unreal 5.8 project")
    else:
        record("engine", "pass" if association.startswith("5.8") else "warning", association if association.startswith("5.8") else "EngineAssociation does not declare a version; confirm through live capabilities")
    plugin_entries = data.get("Plugins", [])
    if not isinstance(plugin_entries, list):
        plugin_entries = []
    enabled = {entry.get("Name") for entry in plugin_entries if isinstance(entry, dict) and entry.get("Enabled") is True}
    missing = {"PythonScriptPlugin", "EditorScriptingUtilities", "UnrealCodexGraph"} - enabled
    record("plugins", "fail" if missing else "pass", "Missing: " + ", ".join(sorted(missing)) if missing else "Required plugins enabled",
           "Run scripts/install.py --project PATH --enable-plugins" if missing else None)
    distribution = Path(__file__).resolve().parents[1]
    for filename in ("action_contract.py", "execute_actions.py", "backend_registry.py", "init_unreal.py"):
        installed = root / "Content/Python" / filename
        source = Path(__file__).with_name(filename) if filename == "action_contract.py" else distribution / "templates/unreal-project/Content/Python" / filename
        fix = "Run scripts/install.py --project PATH --runtime-only, then restart Editor"
        try:
            content = installed.read_text(encoding="utf-8-sig")
            ast.parse(content)
            if source.is_file() and content != source.read_text(encoding="utf-8-sig"):
                record(filename, "fail", "Installed runtime is outdated or modified", fix)
            else:
                record(filename, "pass", "Installed runtime syntax and available source comparison passed")
        except (OSError, SyntaxError, ValueError) as exc:
            record(filename, "fail", str(exc), fix)
    pending_path = root / "Content/Python/harness.pending.json"
    if pending_path.exists():
        try:
            pending = json.loads(pending_path.read_text(encoding="utf-8-sig"))
            run_id = pending["run_id"]
            if not isinstance(run_id, str) or not run_id:
                raise ValueError("invalid pending run_id")
            try:
                result = json.loads((root / "Content/Python/result.json").read_text(encoding="utf-8-sig"))
                validate_response(result, pending)
                complete = True
            except (OSError, ValueError, TypeError, UnrealExecutionError):
                complete = False
            record("pending", "warning" if complete else "fail", "Late response ready for archival" if complete else "Unacknowledged request: " + run_id,
                   None if complete else "Check result.json and the pending document; review execution before retrying")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            record("pending", "fail", "Invalid pending request: " + str(exc), "Review harness.pending.json with Editor closed")
    return root, checks


def diagnose(project, *, offline=False, endpoint=None, model=None, api_key=None, timeout=5.0,
             transport_factory=FileExecutionTransport, models_loader=list_models):
    root, checks = inspect_project(project)
    local_ready = not any(check["status"] == "fail" for check in checks)
    if not offline:
        if local_ready:
            try:
                transport = transport_factory(root, timeout=timeout)
                response = transport.submit({"commands": [{"id": "doctor", "action": "system.capabilities", "arguments": {}}]})
                commands = response.get("commands", [])
                if not response.get("success") or not commands or not isinstance(commands[0].get("data"), dict):
                    raise UnrealExecutionError("Invalid or failed capabilities response")
                capabilities = commands[0]["data"]
                validate_runtime(capabilities)
                checks.append({"name": "editor", "status": "pass", "message": "Watcher responded; engine " + str(capabilities.get("engine_version", "unknown"))})
                checks.append({"name": "graph_bridge", "status": "pass" if capabilities.get("graph_bridge_available") else "warning",
                               "message": "Graph bridge available" if capabilities.get("graph_bridge_available") else "Graph bridge unavailable; graph operations cannot run"})
            except (OSError, RuntimeError, ValueError, TypeError, AttributeError) as exc:
                fix = "Wait for the other CLI task to finish" if "Another harness task" in str(exc) else "Open this project in Editor and check [AI WATCHER] in Output Log"
                checks.append({"name": "editor", "status": "fail", "message": str(exc), "fix": fix})
        else:
            checks.append({"name": "editor", "status": "skipped", "message": "Resolve project/runtime/pending failures first"})
        try:
            models = models_loader(endpoint=endpoint, api_key=api_key, timeout=timeout)
            checks.append({"name": "llm_server", "status": "pass", "message": "Model discovery succeeded; generation is not tested", "models": models})
            selected = model or os.environ.get("UNREAL_HARNESS_LLM_MODEL")
            checks.append({"name": "model", "status": "pass" if selected in models else "fail",
                           "message": "Selected model: " + str(selected) if selected else "No model configured",
                           **({"fix": "Set --model to one of: " + ", ".join(models)} if selected not in models else {})})
        except (ProviderError, OSError, ValueError) as exc:
            fix = "Check the API key and server access permissions" if "HTTP 401" in str(exc) or "HTTP 403" in str(exc) else "Start the model server and check --endpoint (base URL, including /v1 when required)"
            checks.append({"name": "llm_server", "status": "fail", "message": str(exc), "fix": fix})
    return {"success": not any(check["status"] == "fail" for check in checks), "offline": offline, "project": str(root), "checks": checks}


def summarize(report):
    lines = ["Project: " + report["project"]]
    for check in report["checks"]:
        lines.append("{} {}: {}".format(check["status"].upper(), check["name"], check["message"]))
        if "fix" in check:
            lines.append("  Next: " + check["fix"])
        if "models" in check:
            lines.append("  Models: " + ", ".join(check["models"]))
    lines.append("Diagnostics: " + ("PASS (offline)" if report["offline"] else "PASS") if report["success"] else "Diagnostics: FAIL")
    return "\n".join(lines)
