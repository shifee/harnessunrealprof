#!/usr/bin/env python3
"""Run declarative harness benchmark tasks in safe dry-run or Unreal mode."""
import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXECUTOR = ROOT / "templates/unreal-project/Content/Python/execute_actions.py"
CATEGORIES = {"supported", "contract_only", "unsupported"}
CONTRACT_ACTIONS = {"recipe.validate", "recipe.execute", "backend.capabilities", "backend.describe", "backend.operation"}


def load_manifest(path):
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("version") != "0.1" or not isinstance(manifest.get("tasks"), list):
        raise ValueError("manifest must contain version 0.1 and a tasks array")
    ids = set()
    for task in manifest["tasks"]:
        if not isinstance(task, dict) or not isinstance(task.get("id"), str) or not task["id"].strip():
            raise ValueError("each task needs a non-empty id")
        if task["id"] in ids:
            raise ValueError("duplicate task id: " + task["id"])
        ids.add(task["id"])
        if task.get("category") not in CATEGORIES:
            raise ValueError("invalid category for task " + task["id"])
        document = task.get("document")
        if not isinstance(document, dict) or not isinstance(document.get("commands"), list):
            raise ValueError("task {} needs a declarative document with commands".format(task["id"]))
        for command in document["commands"]:
            if not isinstance(command, dict) or not isinstance(command.get("action"), str) or not isinstance(command.get("arguments", {}), dict):
                raise ValueError("invalid command in task " + task["id"])
    return manifest


def classify(task, envelope, native=False):
    if not isinstance(envelope, dict) or envelope.get("success") is not True:
        codes = [str(e.get("code", e.get("error_code", ""))) for e in envelope.get("errors", []) if isinstance(e, dict)] if isinstance(envelope, dict) else []
        if any("unsupported" in code or "unknown_action" in code for code in codes):
            return "unsupported"
        return "failed"
    actions = {c["action"] for c in task["document"]["commands"]}
    if task["category"] == "unsupported":
        return "failed"
    if not native or task["category"] == "contract_only" or (actions and actions <= CONTRACT_ACTIONS):
        return "contract_only"
    return "supported"


def dry_run(document):
    commands = document["commands"]
    if len({c.get("id") for c in commands}) != len(commands) or any(not c.get("id") for c in commands):
        return {"success": False, "errors": [{"code": "invalid_command_id"}]}
    return {"success": True, "dry_run": True, "plan": [{"id": c["id"], "action": c["action"]} for c in commands]}


def run_native(document, project, editor):
    import shutil
    project = Path(project).resolve()
    if project.suffix != ".uproject" or not project.is_file():
        raise ValueError("--native requires an existing .uproject")
    executor = project.parent / "Content/Python/execute_actions.py"
    if not executor.is_file():
        raise ValueError("Harness executor is not installed in project")
    executable = str(editor or shutil.which("UnrealEditor-Cmd") or "")
    if not executable or not Path(executable).is_file():
        raise ValueError("UnrealEditor-Cmd not found; provide --editor-cmd")
    python_dir = executor.parent
    actions, result = python_dir / "actions.json", python_dir / "result.json"
    old_actions = actions.read_bytes() if actions.exists() else None
    old_result = result.read_bytes() if result.exists() else None
    run_id = "benchmark-" + uuid.uuid4().hex
    try:
        document = dict(document, run_id=run_id)
        result.unlink(missing_ok=True)
        actions.write_text(json.dumps(document), encoding="utf-8")
        completed = subprocess.run([executable, str(project), "-run=pythonscript", "-script=" + str(executor), "-unattended", "-nop4", "-nosplash", "-NullRHI"], cwd=str(project.parent), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180, check=False)
        if not result.is_file():
            return {"success": False, "errors": [{"code": "missing_result" if completed.returncode == 0 else "commandlet_failed"}]}
        envelope = json.loads(result.read_text(encoding="utf-8-sig"))
        if envelope.get("run_id") != run_id:
            return {"success": False, "errors": [{"code": "stale_result"}]}
        if completed.returncode != 0:
            return {"success": False, "errors": [{"code": "commandlet_failed", "returncode": completed.returncode}], "result": envelope}
        return envelope
    finally:
        for path, value in ((actions, old_actions), (result, old_result)):
            if value is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(value)


def benchmark(manifest, native=False, project=None, editor=None):
    results = []
    for task in manifest["tasks"]:
        envelope = run_native(task["document"], project, editor) if native else dry_run(task["document"])
        results.append({"id": task["id"], "category": classify(task, envelope, native), "result": envelope})
    counts = {category: sum(item["category"] == category for item in results) for category in ("supported", "contract_only", "unsupported", "failed")}
    total = len(results)
    covered = counts["supported"] if native else 0
    return {"format_version": "0.1", "mode": "native" if native else "contract", "total_tasks": total, "counts": counts, "coverage_percent": round(100 * covered / total, 2) if total and native else None, "tasks": results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", nargs="?", type=Path, default=ROOT / "examples/benchmark-tasks.json")
    parser.add_argument("--native", action="store_true", help="Execute each task through UnrealEditor-Cmd")
    parser.add_argument("--project", type=Path)
    parser.add_argument("--editor-cmd", type=Path)
    parser.add_argument("--report", type=Path, help="Write generated JSON report")
    args = parser.parse_args(argv)
    if args.native and not args.project:
        parser.error("--native requires --project")
    report = benchmark(load_manifest(args.manifest), args.native, args.project, args.editor_cmd)
    output = json.dumps(report, indent=2) + "\n"
    if args.report:
        args.report.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0 if report["counts"]["failed"] == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print("FAIL: {}".format(error), file=sys.stderr)
        sys.exit(2)
