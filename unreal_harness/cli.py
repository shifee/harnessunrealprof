"""Command line interface for deterministic Unreal action documents."""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Callable, Sequence

from .agent import AgentRun
from .execution import FileExecutionTransport
from .providers import OpenAICompatiblePlanner, ProviderError

def _json_value(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError("value must be valid JSON") from exc


def _vector(value: str) -> list[float]:
    parsed = _json_value(value)
    if not isinstance(parsed, list) or len(parsed) != 3 or not all(isinstance(x, (int, float)) for x in parsed):
        raise argparse.ArgumentTypeError("vector must be a JSON array of three numbers")
    return parsed


def _command(action: str, arguments: dict[str, Any], ident: str) -> dict[str, Any]:
    return {"id": ident, "action": action, "depends_on": [], "arguments": arguments}


def build_document(args: argparse.Namespace) -> dict[str, Any]:
    """Build an actions document without contacting Unreal."""
    if args.command == "run-json":
        path = Path(args.document)
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
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
    parser.add_argument("--timeout", type=float, default=30.0)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m unreal_harness", description="Submit deterministic Unreal harness actions")
    sub = parser.add_subparsers(dest="command", required=True)
    def command(name: str) -> argparse.ArgumentParser:
        child = sub.add_parser(name); _common(child); return child
    command("capabilities")
    p = command("inspect-level"); p.add_argument("--query"); p.add_argument("--class", dest="class_name"); p.add_argument("--selected-only", action="store_true"); p.add_argument("--limit", type=int, default=200)
    p = command("list-assets"); p.add_argument("--path", default="/Game"); p.add_argument("--recursive", action=argparse.BooleanOptionalAction, default=True); p.add_argument("--query"); p.add_argument("--class", dest="asset_class"); p.add_argument("--limit", type=int, default=200); p.add_argument("--offset", type=int, default=0)
    p = command("spawn-actor"); p.add_argument("--level", default="current"); p.add_argument("--class", dest="actor_class", required=True); p.add_argument("--actor-label"); p.add_argument("--location", type=_vector, required=True); p.add_argument("--rotation", type=_vector); p.add_argument("--scale", type=_vector); p.add_argument("--save", action="store_true")
    p = command("create-blueprint"); p.add_argument("--folder", required=True); p.add_argument("--name", required=True); p.add_argument("--parent-class", default="/Script/Engine.Actor"); p.add_argument("--save", action="store_true")
    p = command("create-material"); p.add_argument("--folder", required=True); p.add_argument("--name", required=True); p.add_argument("--base-color", type=_json_value, default=[1.0, 1.0, 1.0, 1.0]); p.add_argument("--metallic", type=float, default=0.0); p.add_argument("--roughness", type=float, default=0.5); p.add_argument("--save", action="store_true")
    p = command("set-property"); p.add_argument("--actor-label", required=True); p.add_argument("--property", required=True); p.add_argument("--value", type=_json_value, required=True); p.add_argument("--save", action="store_true")
    p = command("run-json"); p.add_argument("document")
    p = command("ask"); p.add_argument("task"); p.add_argument("--model"); p.add_argument("--endpoint"); p.add_argument("--api-key")
    return parser


def main(argv: Sequence[str] | None = None, *, transport_factory: Callable[..., Any] = FileExecutionTransport) -> int:
    parser = make_parser()
    try:
        args = parser.parse_args(argv)
        if args.command == "ask":
            project = Path(args.project).expanduser()
            if project.suffix.lower() == ".uproject":
                project = project.parent
            transport = transport_factory(project, timeout=args.timeout)
            planner = OpenAICompatiblePlanner(endpoint=args.endpoint, model=args.model, api_key=args.api_key, timeout=args.timeout)
            result = AgentRun(transport, planner).run(args.task)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0 if result.get("success") else 1
        document = build_document(args)
        project = Path(args.project).expanduser()
        if project.suffix.lower() == ".uproject":
            project = project.parent
        transport = transport_factory(project, timeout=args.timeout)
        result = transport.submit(document, timeout=args.timeout)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (ValueError, OSError, TypeError, RuntimeError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
