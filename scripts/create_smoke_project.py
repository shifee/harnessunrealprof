#!/usr/bin/env python3
"""Create a disposable Unreal Engine 5.8 project for harness smoke tests."""

import argparse
import json
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = REPO_ROOT / "templates" / "unreal-project"
SKILL_ROOT = REPO_ROOT / ".agents" / "skills" / "unreal-game-builder"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Directory to create")
    parser.add_argument("--force", action="store_true", help="Replace an existing output directory")
    parser.add_argument("--graph-plugin-package", type=Path, help="Built UnrealCodexGraph plugin package to install")
    return parser.parse_args(argv)


def create_project(output, force=False, graph_plugin_package=None):
    output = output.expanduser().resolve()
    if output.exists():
        if not force:
            raise ValueError("Output already exists; pass --force to replace it: {}".format(output))
        shutil.rmtree(str(output))
    output.mkdir(parents=True)
    for source in TEMPLATE_ROOT.iterdir():
        if source.name == "UnrealCodexGraph.uplugin":
            continue
        if source.name == "UnrealCodexSmoke.uproject":
            continue
        if source.name == "UnrealCodexHarnessSmoke.uproject":
            project_data = json.loads(source.read_text(encoding="utf-8"))
            if graph_plugin_package:
                project_data["Plugins"].append({"Name": "UnrealCodexGraph", "Enabled": True})
            (output / source.name).write_text(json.dumps(project_data, indent=2) + "\n", encoding="utf-8")
        elif source.name == "Content":
            shutil.copytree(str(source), str(output / source.name))
        elif source.name == "Plugins":
            if graph_plugin_package:
                shutil.copytree(str(graph_plugin_package), str(output / source.name / "UnrealCodexGraph"))
            else:
                shutil.copytree(str(source), str(output / source.name), ignore=shutil.ignore_patterns("UnrealCodexGraph"))
    return output / "UnrealCodexHarnessSmoke.uproject"


def main(argv=None):
    args = parse_args(argv)
    try:
        project = create_project(args.output, args.force, args.graph_plugin_package.expanduser().resolve() if args.graph_plugin_package else None)
    except (OSError, ValueError) as error:
        print("ERROR: {}".format(error))
        return 2
    print("Created UE 5.8 smoke project: {}".format(project))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
