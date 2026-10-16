#!/usr/bin/env python3
"""Verify built artifacts away from the checkout, without model or Editor services."""
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command, cwd, env, input_text=None):
    subprocess.run([str(value) for value in command], cwd=cwd, env=env, check=True, timeout=180,
                   input=input_text, text=True, encoding="utf-8")


def main():
    wheels = list((ROOT / "dist").glob("*.whl"))
    sources = list((ROOT / "dist").glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        raise ValueError("Expected exactly one wheel and one sdist in dist/")
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    with tempfile.TemporaryDirectory(prefix="harness-distribution-") as temporary:
        workspace = Path(temporary)
        with tarfile.open(sources[0]) as archive:
            for member in archive.getmembers():
                target = (workspace / member.name).resolve()
                if workspace not in target.parents or not (member.isdir() or member.isfile()):
                    raise ValueError("Invalid sdist member: " + member.name)
                if member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.extractfile(member) as stream:
                        target.write_bytes(stream.read())
        source_roots = list(workspace.glob("*/pyproject.toml"))
        if len(source_roots) != 1:
            raise ValueError("Expected one source project in sdist")
        run([sys.executable, "scripts/check_release.py"], source_roots[0].parent, env)
        environment = workspace / "venv"
        run([sys.executable, "-m", "venv", environment], workspace, env)
        bin_dir = environment / ("Scripts" if os.name == "nt" else "bin")
        python = bin_dir / ("python.exe" if os.name == "nt" else "python")
        console = bin_dir / ("unreal-harness.exe" if os.name == "nt" else "unreal-harness")
        run([python, "-m", "pip", "install", "--no-index", "--no-deps", wheels[0]], workspace, env)
        run([python, "-I", "-c",
             "import unreal_harness; from pathlib import Path; import sys; "
             "assert Path(sys.prefix) in Path(unreal_harness.__file__).resolve().parents; "
             "print(unreal_harness.__file__)"], workspace, env)
        run([console, "--help"], workspace, env)
        for command in ("start", "chat", "ask", "resume", "doctor"):
            run([console, command, "--help"], workspace, env)
        project = workspace / "project"
        project.mkdir()
        (project / "Test.uproject").write_text(json.dumps({"EngineAssociation": "5.8"}), encoding="utf-8")
        run([console, "tasks", "--project", project, "--json"], workspace, env)
        runtime = project / "Content/Python"
        runtime.mkdir(parents=True)
        (runtime / "execute_actions.py").write_text("# distribution fixture", encoding="utf-8")
        settings = workspace / "config/UnrealHarness/settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({"version": 1, "project": str(project / "Test.uproject"),
                                        "auto_editor": False}), encoding="utf-8")
        env.update(APPDATA=str(workspace / "config"), XDG_CONFIG_HOME=str(workspace / "config"))
        run([console, "start", "--no-editor"], workspace, env, input_text="/exit\n")
    print("PASS isolated sdist checks and installed wheel CLI")
    return 0


if __name__ == "__main__":
    sys.exit(main())
