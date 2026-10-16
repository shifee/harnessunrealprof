#!/usr/bin/env python3
"""Run reproducible host gates; native checks are opt-in and never start services."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", help="Also diagnose and validate an installed project offline")
    parser.add_argument("--native", action="store_true", help="Run fixtures against an already open Editor; requires --project")
    parser.add_argument("--build", action="store_true", help="Build sdist and wheel; requires the build package")
    parser.add_argument("--lint", action="store_true", help="Check undefined names with Ruff; requires the ruff package")
    parser.add_argument("--output", type=Path, default=ROOT / "Saved/HarnessReadiness/host.json")
    args = parser.parse_args(argv)
    if args.native and not args.project:
        parser.error("--native requires --project")
    commands = [
        ("unit_and_protocol_tests", ["-m", "unittest", "discover", "-s", "tests", "-q"]),
        ("action_catalog", ["scripts/export_action_contract.py", "--check"]),
        ("eval_suite", ["scripts/run_agent_evals.py", "--validate-only"]),
    ]
    if args.lint:
        commands.insert(0, ("undefined_names", ["-m", "ruff", "check", "unreal_harness", "scripts", "templates/unreal-project/Content/Python", "--select", "F821"]))
    if args.project:
        commands.extend([
            ("project_offline", ["-m", "unreal_harness", "doctor", "--project", args.project, "--offline", "--json"]),
            ("installation", ["scripts/validate_install.py", str(Path(args.project).parent if Path(args.project).suffix.lower() == ".uproject" else Path(args.project))]),
        ])
    if args.build:
        commands.append(("distribution_build", ["-m", "build"]))
        commands.append(("distribution_install", ["scripts/check_distribution.py"]))
    if args.native:
        commands.append(("native_lifecycle", ["scripts/run_native_acceptance.py", "--project", args.project]))
    records = []
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    for name, arguments in commands:
        try:
            result = subprocess.run([sys.executable, *arguments], cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, encoding="utf-8", errors="replace", timeout=600)
            record = {"name": name, "success": result.returncode == 0, "exit_code": result.returncode, "output": result.stdout[-24000:]}
        except (OSError, subprocess.TimeoutExpired) as exc:
            record = {"name": name, "success": False, "error": str(exc)}
        records.append(record)
        print(("PASS " if record["success"] else "FAIL ") + name, flush=True)
        if not record["success"]:
            print(record.get("output", record.get("error")))
            break
    report = {"success": len(records) == len(commands) and all(r["success"] for r in records),
              "requested_gates": len(commands), "gates": records, "native_requested": args.native, "model_inference_tested": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print("Report: " + str(args.output))
    print("Model inference: not tested; this command never starts LM Studio or any model service")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
