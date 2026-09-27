#!/usr/bin/env python3
"""Create a disposable Unreal Engine 5.8 project for harness smoke tests."""

import argparse
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = REPO_ROOT / "templates" / "unreal-project"
SKILL_ROOT = REPO_ROOT / ".agents" / "skills" / "unreal-game-builder"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Directory to create")
    parser.add_argument("--force", action="store_true", help="Replace an existing output directory")
    return parser.parse_args(argv)


def create_project(output, force=False):
    output = output.expanduser().resolve()
    if output.exists():
        if not force:
            raise ValueError("Output already exists; pass --force to replace it: {}".format(output))
        shutil.rmtree(str(output))
    output.mkdir(parents=True)
    for source in TEMPLATE_ROOT.iterdir():
        if source.name == "UnrealCodexHarnessSmoke.uproject":
            shutil.copy2(str(source), str(output / source.name))
        elif source.name in {"Content", "Plugins"}:
            shutil.copytree(str(source), str(output / source.name))
    skill_destination = output / ".agents/skills/unreal-game-builder"
    shutil.copytree(str(SKILL_ROOT), str(skill_destination))
    return output / "UnrealCodexHarnessSmoke.uproject"


def main(argv=None):
    args = parse_args(argv)
    try:
        project = create_project(args.output, args.force)
    except (OSError, ValueError) as error:
        print("ERROR: {}".format(error))
        return 2
    print("Created UE 5.8 smoke project: {}".format(project))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
