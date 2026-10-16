#!/usr/bin/env python3
"""Install the Unreal Codex harness into an existing Unreal Engine project."""

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = REPO_ROOT / "templates" / "unreal-project"
SKILL_ROOT = REPO_ROOT / ".agents" / "skills" / "unreal-game-builder"
REQUIRED_PLUGINS = (
    "PythonScriptPlugin",
    "EditorScriptingUtilities",
    "UnrealCodexGraph",
)
NATIVE_MCP_PLUGINS = (
    "ModelContextProtocol",
    "AllToolsets",
)
MAX_SEARCH_DEPTH = 4
SKIP_DIRS = {
    ".git",
    ".idea",
    ".vscode",
    "Binaries",
    "DerivedDataCache",
    "Intermediate",
    "node_modules",
    "Saved",
    "__pycache__",
}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--project", type=Path, help="Path to a .uproject file")
    group.add_argument("--project-dir", type=Path, help="Directory containing a .uproject")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    parser.add_argument("--force", action="store_true", help="Back up and replace conflicts")
    parser.add_argument("--runtime-only", action="store_true", help="Update Python runtime with backups, preserving commands, skills and plugins")
    parser.add_argument(
        "--enable-plugins",
        action="store_true",
        help="Back up the .uproject and enable required editor plugins",
    )
    parser.add_argument(
        "--enable-native-mcp",
        action="store_true",
        help="Also enable Unreal 5.8's experimental MCP server and default toolsets",
    )
    return parser.parse_args(argv)


def direct_projects(directory):
    if not directory.is_dir():
        return []
    return sorted(path.resolve() for path in directory.glob("*.uproject") if path.is_file())


def validate_project_file(path):
    path = path.expanduser().resolve()
    if path.suffix.lower() != ".uproject":
        raise ValueError("Project path must end with .uproject: {}".format(path))
    if not path.is_file():
        raise ValueError("Project file does not exist: {}".format(path))
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Invalid .uproject JSON: {}".format(error))
    if not isinstance(data, dict):
        raise ValueError("The .uproject root must be a JSON object")
    association = str(data.get("EngineAssociation", "")).strip()
    if re.match(r"^\d+\.\d+", association) and not association.startswith("5.8"):
        raise ValueError(
            "This harness targets Unreal Engine 5.8; project EngineAssociation is " + association
        )
    return path, data


def recursive_projects(start, max_depth=MAX_SEARCH_DEPTH):
    start = start.resolve()
    found = []
    stack = [(start, 0)]
    while stack:
        directory, depth = stack.pop()
        try:
            entries = list(directory.iterdir())
        except (OSError, PermissionError):
            continue
        for entry in entries:
            if entry.is_file() and entry.suffix.lower() == ".uproject":
                found.append(entry.resolve())
        if depth >= max_depth:
            continue
        for entry in entries:
            if entry.is_dir() and entry.name not in SKIP_DIRS and not entry.is_symlink():
                stack.append((entry, depth + 1))
    return sorted(set(found))


def choose_project(candidates):
    candidates = sorted(set(candidates))
    if not candidates:
        raise ValueError("No Unreal project found. Pass --project PATH_TO_PROJECT.uproject")
    if len(candidates) == 1:
        return candidates[0]
    print("Multiple Unreal projects found:")
    for index, path in enumerate(candidates, 1):
        print("  {}. {}".format(index, path))
    if not sys.stdin.isatty():
        raise ValueError("Multiple projects found in non-interactive mode; pass --project")
    while True:
        answer = input("Choose a project number: ").strip()
        try:
            selected = int(answer)
        except ValueError:
            selected = 0
        if 1 <= selected <= len(candidates):
            return candidates[selected - 1]
        print("Enter a number from 1 to {}.".format(len(candidates)))


def find_project(args, cwd):
    if args.project:
        return args.project.expanduser().resolve()
    if args.project_dir:
        candidates = direct_projects(args.project_dir.expanduser().resolve())
        return choose_project(candidates)

    current = cwd.resolve()
    for directory in (current,) + tuple(current.parents):
        candidates = direct_projects(directory)
        if candidates:
            return choose_project(candidates)
    return choose_project(recursive_projects(current))


def backup_path(path):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(path.name + ".bak." + stamp)
    counter = 1
    while candidate.exists():
        candidate = path.with_name(path.name + ".bak.{}.{}".format(stamp, counter))
        counter += 1
    return candidate


def copy_file(source, destination, dry_run, force, report):
    if destination.exists() and source.read_bytes() == destination.read_bytes():
        report["skipped"].append(destination)
        return
    if destination.exists() and not force:
        report["skipped"].append(destination)
        return
    if destination.exists():
        backup = backup_path(destination)
        if not dry_run:
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(destination), str(backup))
        report["backups"].append(backup)
        report["replaced"].append(destination)
    else:
        report["created"].append(destination)
    if not dry_run:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(source), str(destination))


def plugin_status(project_data, plugin_names):
    entries = project_data.get("Plugins", [])
    enabled = {
        entry.get("Name")
        for entry in entries
        if isinstance(entry, dict) and entry.get("Enabled") is True
    }
    return {name: name in enabled for name in plugin_names}


def enable_plugins(project_file, project_data, plugin_names, dry_run, report):
    plugins = project_data.get("Plugins")
    if not isinstance(plugins, list):
        plugins = []
        project_data["Plugins"] = plugins
    by_name = {
        entry.get("Name"): entry
        for entry in plugins
        if isinstance(entry, dict) and isinstance(entry.get("Name"), str)
    }
    changed = False
    for name in plugin_names:
        if name in by_name:
            if by_name[name].get("Enabled") is not True:
                by_name[name]["Enabled"] = True
                changed = True
        else:
            plugins.append({"Name": name, "Enabled": True})
            changed = True
    if not changed:
        return
    backup = backup_path(project_file)
    report["backups"].append(backup)
    report["plugins_enabled"].extend(plugin_names)
    if not dry_run:
        shutil.copy2(str(project_file), str(backup))
        with project_file.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(project_data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")


def print_report(project_file, dry_run, report, required_status, native_status):
    print("Project: {}".format(project_file))
    print("Mode: {}".format("dry-run" if dry_run else "install"))
    for key, label in (
        ("created", "Created"),
        ("replaced", "Replaced"),
        ("skipped", "Skipped"),
        ("backups", "Backups"),
    ):
        print("{} ({}):".format(label, len(report[key])))
        for path in report[key]:
            print("  - {}".format(path))
    if report["plugins_enabled"]:
        print("{} plugins: {}".format(
            "Would enable" if dry_run else "Enabled",
            ", ".join(report["plugins_enabled"]),
        ))
    missing = [name for name, enabled in required_status.items() if not enabled]
    if missing:
        print("Required plugins not enabled: {}".format(", ".join(missing)))
        print("Enable them in Unreal Editor or rerun with --enable-plugins.")
    else:
        print("Required plugins enabled: {}".format(", ".join(REQUIRED_PLUGINS)))
    native_missing = [name for name, enabled in native_status.items() if not enabled]
    if native_missing:
        print("Optional UE 5.8 native MCP plugins not enabled: {}".format(", ".join(native_missing)))
        print("Enable them manually or rerun with --enable-native-mcp.")
    else:
        print("Optional UE 5.8 native MCP enabled: {}".format(", ".join(NATIVE_MCP_PLUGINS)))


def install(args):
    project_file, project_data = validate_project_file(find_project(args, Path.cwd()))
    project_root = project_file.parent
    report = {
        "created": [],
        "replaced": [],
        "skipped": [],
        "backups": [],
        "plugins_enabled": [],
    }
    mappings = [
        (REPO_ROOT / "unreal_harness/action_contract.py", project_root / "Content/Python/action_contract.py"),
        (TEMPLATE_ROOT / "Content/Python/backend_registry.py", project_root / "Content/Python/backend_registry.py"),
        (TEMPLATE_ROOT / "Content/Python/execute_actions.py", project_root / "Content/Python/execute_actions.py"),
        (TEMPLATE_ROOT / "Content/Python/init_unreal.py", project_root / "Content/Python/init_unreal.py"),
        (TEMPLATE_ROOT / "Content/Python/actions.example.json", project_root / "Content/Python/actions.json"),
        (SKILL_ROOT / "SKILL.md", project_root / ".agents/skills/unreal-game-builder/SKILL.md"),
        (SKILL_ROOT / "agents/openai.yaml", project_root / ".agents/skills/unreal-game-builder/agents/openai.yaml"),
        (SKILL_ROOT / "references/actions-schema.md", project_root / ".agents/skills/unreal-game-builder/references/actions-schema.md"),
    ]
    plugin_root = TEMPLATE_ROOT / "Plugins/UnrealCodexGraph"
    for source in sorted(path for path in plugin_root.rglob("*") if path.is_file()):
        mappings.append((source, project_root / source.relative_to(TEMPLATE_ROOT)))
    runtime_only = getattr(args, "runtime_only", False)
    if runtime_only:
        if args.enable_plugins or args.enable_native_mcp:
            raise ValueError("--runtime-only cannot change plugins; omit --enable-plugins and --enable-native-mcp")
        mappings = [(source, target) for source, target in mappings if target.parent == project_root / "Content/Python" and target.suffix == ".py"]
    for source, destination in mappings:
        if not source.is_file():
            raise ValueError("Distribution file missing: {}".format(source))
        copy_file(source, destination, args.dry_run, args.force or runtime_only, report)

    plugins_to_enable = []
    if args.enable_plugins:
        plugins_to_enable.extend(REQUIRED_PLUGINS)
    if args.enable_native_mcp:
        plugins_to_enable.extend(NATIVE_MCP_PLUGINS)
    if plugins_to_enable:
        enable_plugins(
            project_file,
            project_data,
            tuple(dict.fromkeys(plugins_to_enable)),
            args.dry_run,
            report,
        )
    required_status = plugin_status(project_data, REQUIRED_PLUGINS)
    native_status = plugin_status(project_data, NATIVE_MCP_PLUGINS)
    print_report(project_file, args.dry_run, report, required_status, native_status)

    if not args.dry_run:
        print("Install files verified: {}".format(all(path.is_file() for _, path in mappings)))
    return 0


def main():
    try:
        return install(parse_args())
    except (ValueError, OSError) as error:
        print("ERROR: {}".format(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
