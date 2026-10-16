"""Command line interface for deterministic Unreal action documents."""
from __future__ import annotations

import argparse
import json
import sys
import uuid
import math
from pathlib import Path
from typing import Any, Callable, Sequence

from .agent import AgentRun
from .task_agent import TaskAgent
from .hooks import Hooks
from .task_store import list_tasks, load_task
from .execution import FileExecutionTransport
from .providers import OpenAICompatiblePlanner, ProviderError, list_models
from .health import diagnose, summarize, inspect_project

def _json_value(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError("value must be valid JSON") from exc


def _vector(value: str) -> list[float]:
    parsed = _json_value(value)
    if not isinstance(parsed, list) or len(parsed) != 3 or not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in parsed):
        raise argparse.ArgumentTypeError("vector must be a JSON array of three numbers")
    return parsed


def _positive_timeout(value):
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a finite positive number") from exc
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("timeout must be a finite positive number")
    return number


def _command(action: str, arguments: dict[str, Any], ident: str) -> dict[str, Any]:
    return {"id": ident, "action": action, "depends_on": [], "arguments": arguments}


def build_document(args: argparse.Namespace) -> dict[str, Any]:
    """Build an actions document without contacting Unreal."""
    if args.command == "run-json":
        path = Path(args.document)
        try:
            document = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("cannot read JSON document {}: {}".format(path, exc)) from exc
        if not isinstance(document, dict) or not isinstance(document.get("commands"), list):
            raise ValueError("JSON document must be an object with a commands array")
        document = dict(document)
        document["run_id"] = str(uuid.uuid4())
        return document

    command_name = args.command
    if command_name == "capabilities":
        action, arguments = "system.capabilities", {}
    elif command_name == "inspect-level":
        action = "level.inspect"
        arguments = {"limit": args.limit}
        if args.query is not None: arguments["query"] = args.query
        if args.class_name is not None: arguments["class"] = args.class_name
        if args.selected_only: arguments["selected_only"] = True
    elif command_name == "list-assets":
        action = "asset.search"
        arguments = {"path": args.path, "recursive": args.recursive, "limit": args.limit, "offset": args.offset}
        if args.query is not None: arguments["query"] = args.query
        if args.asset_class is not None: arguments["class"] = args.asset_class
    elif command_name == "spawn-actor":
        action = "level.spawn_actor"
        arguments = {"level": args.level, "class": args.actor_class, "transform": {"location": args.location}}
        if args.rotation is not None: arguments["transform"]["rotation"] = args.rotation
        if args.scale is not None: arguments["transform"]["scale"] = args.scale
        if args.actor_label is not None: arguments["actor_label"] = args.actor_label
    elif command_name == "create-blueprint":
        action, arguments = "blueprint.create", {"folder": args.folder, "name": args.name, "parent_class": args.parent_class}
    elif command_name == "create-material":
        action, arguments = "material.create", {"folder": args.folder, "name": args.name, "base_color": args.base_color, "metallic": args.metallic, "roughness": args.roughness}
    elif command_name == "set-property":
        action, arguments = "level.set_actor_property", {"actor_label": args.actor_label, "property": args.property, "value": args.value}
    else:
        raise ValueError("unknown command: {}".format(command_name))
    commands = [_command(action, arguments, command_name.replace("-", "_"))]
    if getattr(args, "save", False):
        commands.append({"id": "save", "action": "project.save", "depends_on": [commands[-1]["id"]], "arguments": {"save_level": True, "save_assets": True}})
    return {"format_version": "1.0", "dry_run": False, "run_id": str(uuid.uuid4()), "commands": commands}


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", required=True, help="Unreal project root or .uproject path")
    parser.add_argument("--timeout", type=_positive_timeout, default=30.0)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m unreal_harness", description="Submit deterministic Unreal harness actions")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("start", help="Open guided interactive CLI with saved settings")
    p.add_argument("--project"); p.add_argument("--endpoint"); p.add_argument("--model")
    p.add_argument("--no-editor", action="store_true"); p.add_argument("--setup", action="store_true")
    def command(name: str) -> argparse.ArgumentParser:
        child = sub.add_parser(name); _common(child); return child
    command("capabilities")
    p = command("doctor"); p.set_defaults(timeout=5.0)
    p.add_argument("--offline", action="store_true"); p.add_argument("--json", action="store_true")
    p.add_argument("--endpoint"); p.add_argument("--model"); p.add_argument("--api-key")
    p = sub.add_parser("models", help="List model IDs without generating a plan")
    p.add_argument("--endpoint"); p.add_argument("--api-key"); p.add_argument("--json", action="store_true")
    p.add_argument("--timeout", type=_positive_timeout, default=5.0)
    p = command("inspect-level"); p.add_argument("--query"); p.add_argument("--class", dest="class_name"); p.add_argument("--selected-only", action="store_true"); p.add_argument("--limit", type=int, default=200)
    p = command("list-assets"); p.add_argument("--path", default="/Game"); p.add_argument("--recursive", action=argparse.BooleanOptionalAction, default=True); p.add_argument("--query"); p.add_argument("--class", dest="asset_class"); p.add_argument("--limit", type=int, default=200); p.add_argument("--offset", type=int, default=0)
    p = command("spawn-actor"); p.add_argument("--level", default="current"); p.add_argument("--class", dest="actor_class", required=True); p.add_argument("--actor-label"); p.add_argument("--location", type=_vector, required=True); p.add_argument("--rotation", type=_vector); p.add_argument("--scale", type=_vector); p.add_argument("--save", action="store_true")
    p = command("create-blueprint"); p.add_argument("--folder", required=True); p.add_argument("--name", required=True); p.add_argument("--parent-class", default="/Script/Engine.Actor"); p.add_argument("--save", action="store_true")
    p = command("create-material"); p.add_argument("--folder", required=True); p.add_argument("--name", required=True); p.add_argument("--base-color", type=_json_value, default=[1.0, 1.0, 1.0, 1.0]); p.add_argument("--metallic", type=float, default=0.0); p.add_argument("--roughness", type=float, default=0.5); p.add_argument("--save", action="store_true")
    p = command("set-property"); p.add_argument("--actor-label", required=True); p.add_argument("--property", required=True); p.add_argument("--value", type=_json_value, required=True); p.add_argument("--save", action="store_true")
    p = command("run-json"); p.add_argument("document")
    for name in ("ask", "resume", "chat"):
        p = command(name)
        if name == "ask":
            p.add_argument("--preview", action="store_true", help="Validate a single-pass plan without executing it")
        if name != "chat":
            p.add_argument("task" if name == "ask" else "task_id")
        p.add_argument("--model"); p.add_argument("--endpoint"); p.add_argument("--api-key")
        p.add_argument("--json", action="store_true", help="Print full machine-readable report instead of a summary")
        p.add_argument("--read-only", action="store_true", help="Forbid any mutation or save in the generated plan")
        p.add_argument("--verify-json", help="Read-only verification document for tasks without automatic read-back")
        p.add_argument("--max-corrections", type=int, default=2, choices=range(3))
        p.add_argument("--llm-timeout", type=_positive_timeout, default=120.0, help="Timeout for each model request, separate from Unreal response timeout")
        p.add_argument("--max-steps", type=int, choices=range(2, 31), default=8)
        p.add_argument("--max-commands", type=int, choices=range(1, 201), default=80)
        p.add_argument("--task-timeout", type=_positive_timeout, default=300.0)
        p.add_argument("--hooks", help="Explicit path to a local lifecycle hook configuration")
        p.add_argument("--single-pass", action="store_true", help="Use the original single-plan workflow")
        if name in {"ask", "chat"}:
            p.add_argument("--session", help="Task ID providing context for a new request")
    p = command("tasks"); p.add_argument("--task-id"); p.add_argument("--json", action="store_true")
    return parser


def _ask_clarification(clarification: dict[str, Any]) -> bool:
    if not sys.stdin.isatty():
        return False
    question = clarification.get("question", "Please clarify the task")
    print(question, file=sys.stderr)
    options = clarification.get("options", [])
    for index, option in enumerate(options, 1):
        print("{}. {}".format(index, option), file=sys.stderr)
    try:
        answer = input("Answer (or blank to cancel): ").strip()
    except EOFError:
        return False
    if not answer:
        return False
    clarification["answer"] = answer
    return True

def _confirm_high_risk(summary: dict[str, Any]) -> bool:
    if not sys.stdin.isatty():
        return False
    print("High-risk actions:", file=sys.stderr)
    for command in summary["high_risk_commands"]:
        print("- {} {}".format(command.get("action"), json.dumps(command.get("arguments", {}), ensure_ascii=False)), file=sys.stderr)
    try:
        return input("Execute these actions? [y/N] ").strip().lower() in {"y", "yes"}
    except EOFError:
        return False


def _human_summary(result: dict[str, Any]) -> str:
    runtime = result.get("result", result)
    lines = []
    if result.get("success"):
        lines.append("Preview ready; no mutation executed." if result.get("phase") == "preview" else "Success: Unreal state read." if result.get("read_only") else
                     "Success: execution and read-back completed." if result.get("phase") == "verify" else "Success: execution completed.")
        if result.get("phase") == "preview":
            lines.append(json.dumps(result.get("document", result.get("plan")), ensure_ascii=False, indent=2))
    else:
        lines.append("Failed during {}.".format(result.get("phase", "execution")))
        error = result.get("error")
        if not error:
            failed = next((c for c in runtime.get("commands", []) if not c.get("success", True)), None)
            if failed:
                error = "{} ({}): {}".format(failed.get("id"), failed.get("action"), failed.get("error"))
            elif runtime.get("errors"):
                error = runtime["errors"][0].get("message")
        if error:
            lines.append(str(error))
        if result.get("execution_succeeded"):
            lines.append("Changes executed, but verification is incomplete or failed.")
    for command in runtime.get("commands", []):
        if not command.get("success") or command.get("skipped"):
            continue
        data = command.get("data", {})
        if result.get("read_only"):
            lines.append(str(command.get("action", "Read result")) + ":\n" + json.dumps(data, ensure_ascii=False, indent=2))
            continue
        path = data.get("asset_path", data.get("path"))
        if path:
            lines.append("{}: {}".format(command["action"], path))
        if "total" in data:
            lines.append("{}: {} found{}".format(command["action"], data["total"], " (output truncated)" if data.get("truncated") else ""))
        for actor in data.get("actors", []):
            lines.append("{}: location {}".format(actor.get("label", actor.get("name")), actor.get("location")))
    if result.get("task_id"):
        lines.append("Task ID: " + result["task_id"])
    changed = result.get("changed_objects", runtime.get("changed_objects", []))
    if changed:
        lines.append("Changed objects: " + ", ".join(changed))
        if not result.get("success"):
            lines.append("Review these objects before retrying.")
    if result.get("audit_path", runtime.get("audit_path")):
        lines.append("Audit: " + result.get("audit_path", runtime.get("audit_path")))
    for item in result.get("acceptance", []):
        lines.append("{}: {}".format("PASS" if item["success"] else "FAIL", item["description"]))
    return "\n".join(lines)


def _chat(args, agent, verification):
    session = args.session
    print("Harness chat. Type a prompt; /new clears context, /tasks lists tasks, /resume ID continues one, /exit quits.")
    while True:
        try:
            task = input("harness> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("Chat closed.")
            return 0
        if not task:
            continue
        if task == "/exit":
            return 0
        if task == "/new":
            session = None
            print("Next prompt starts a new task.")
            continue
        if task == "/tasks":
            print(json.dumps(list_tasks(args.project), ensure_ascii=False, indent=2))
            continue
        if task.startswith("/") and not task.startswith("/resume "):
            print("Unknown chat command. Use /new, /tasks, /resume ID or /exit.")
            continue
        project, checks = inspect_project(args.project)
        if any(check["status"] == "fail" for check in checks):
            print(summarize({"success": False, "project": str(project), "checks": checks, "offline": True}))
            continue
        try:
            if task.startswith("/resume "):
                task_id = task.removeprefix("/resume ").strip()
                if args.read_only and not load_task(args.project, task_id)["read_only"]:
                    raise ValueError("Read-only chat cannot resume a task that permits mutations")
                result = agent.resume(task_id)
            else:
                result = agent.run(task, read_only=args.read_only, verify=verification, session=session)
        except KeyboardInterrupt:
            print("Task interrupted. Review its checkpoint and pending request before continuing.")
            return 130
        except (ValueError, OSError, RuntimeError) as exc:
            print("Failed: " + str(exc))
            continue
        print(_human_summary(result))
        if result.get("audit_path"):
            session = result["task_id"]


def main(argv: Sequence[str] | None = None, *, transport_factory: Callable[..., Any] = FileExecutionTransport, result_callback=None, progress_callback=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] == "start":
        from .interactive import launch
        return launch(arguments[1:] if arguments else [], cli_main=lambda values, **kwargs: main(values, transport_factory=transport_factory, **kwargs))
    parser = make_parser()
    try:
        args = parser.parse_args(arguments)
        if args.command == "models":
            models = list_models(endpoint=args.endpoint, api_key=args.api_key, timeout=args.timeout)
            print(json.dumps({"models": models}, ensure_ascii=False) if args.json else "\n".join(models) or "No models listed by server")
            return 0
        if args.command == "doctor":
            report = diagnose(args.project, offline=args.offline, endpoint=args.endpoint, model=args.model,
                              api_key=args.api_key, timeout=args.timeout, transport_factory=transport_factory)
            print(json.dumps(report, ensure_ascii=False) if args.json else summarize(report))
            return 0 if report["success"] else 1
        if args.command == "tasks":
            result = load_task(args.project, args.task_id) if args.task_id else list_tasks(args.project)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command in {"ask", "resume", "chat"}:
            if getattr(args, "preview", False):
                args.single_pass = True
            if args.command == "chat" and (args.single_pass or args.json):
                raise ValueError("Interactive chat requires the task agent and human-readable output")
            if args.command == "resume" and (args.single_pass or args.read_only or args.verify_json):
                raise ValueError("Resume preserves stored settings; --single-pass, --read-only and --verify-json cannot override them")
            if args.command == "ask" and args.single_pass and args.session:
                raise ValueError("--session requires the task agent")
            if args.single_pass and args.hooks:
                raise ValueError("--hooks requires the task agent")
            hooks = Hooks.from_file(args.hooks) if args.hooks else None
            project, checks = inspect_project(args.project)
            if any(check["status"] == "fail" for check in checks):
                report = {"success": False, "phase": "preflight", "project": str(project), "checks": checks, "offline": True}
                print(json.dumps(report, ensure_ascii=False) if args.json else summarize(report))
                return 1
            transport = transport_factory(project, timeout=args.timeout)
            planner = OpenAICompatiblePlanner(endpoint=args.endpoint, model=args.model, api_key=args.api_key, timeout=args.llm_timeout, max_corrections=args.max_corrections)
            verification = json.loads(Path(args.verify_json).read_text(encoding="utf-8-sig")) if args.verify_json else None
            agent = AgentRun(transport, planner) if args.single_pass else TaskAgent(transport, planner,
                    max_steps=args.max_steps, max_commands=args.max_commands, task_timeout=args.task_timeout, hooks=hooks, progress_callback=progress_callback)
            if args.command == "chat":
                return _chat(args, agent, verification)
            if args.command == "resume":
                result = agent.resume(args.task_id)
            elif args.session:
                result = agent.run(args.task, read_only=args.read_only, verify=verification, session=args.session)
            else:
                if args.single_pass:
                    task = args.task
                    for _ in range(5):
                        result = agent.run(task, read_only=args.read_only, verify=verification,
                                           preview=getattr(args, "preview", False), approve=_confirm_high_risk)
                        if result.get("phase") != "clarification" or not result.get("clarification") or not _ask_clarification(result["clarification"]):
                            break
                        task += "\nUser clarification: " + result["clarification"]["answer"]
                else:
                    result = agent.run(args.task, read_only=args.read_only, verify=verification)
            if result_callback:
                result_callback(result)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True) if args.json else _human_summary(result))
            return 0 if result.get("success") else 1
        document = build_document(args)
        project = Path(args.project).expanduser()
        if project.suffix.lower() == ".uproject":
            project = project.parent
        transport = transport_factory(project, timeout=args.timeout)
        result = transport.submit(document, timeout=args.timeout)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        print(_human_summary(result), file=sys.stderr)
        return 0 if result.get("success", False) else 1
    except (ValueError, OSError, TypeError, RuntimeError) as exc:
        if 'args' in locals() and getattr(args, "json", False):
            print(json.dumps({"success": False, "phase": "configuration_or_transport", "error": str(exc)}, ensure_ascii=False))
            return 2
        print("error: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
