#!/usr/bin/env python3
"""Evaluate actual user prompts against independently authored Unreal checks."""
import argparse
import json
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from unreal_harness.evaluation import load_suite, render, score
from unreal_harness.execution import FileExecutionTransport
from unreal_harness.health import inspect_project
from unreal_harness.providers import OpenAICompatiblePlanner
from unreal_harness.task_agent import TaskAgent


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=ROOT / "evals/agent-tasks.json")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--project")
    parser.add_argument("--model")
    parser.add_argument("--endpoint")
    parser.add_argument("--api-key")
    parser.add_argument("--trials", type=int, choices=range(1, 11), default=1)
    parser.add_argument("--max-steps", type=int, choices=range(2, 31), default=8)
    parser.add_argument("--task-timeout", type=float, default=300)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        suite = load_suite(args.suite)
        if args.validate_only:
            print("Suite valid: {} prompts and golden checks. Unreal/model execution was not tested.".format(len(suite["cases"])))
            return 0
        if not args.project:
            raise ValueError("Live evaluation requires --project; use --validate-only for offline validation")
        project, checks = inspect_project(args.project)
        if any(c["status"] == "fail" for c in checks):
            raise ValueError("Project preflight failed: " + "; ".join(c["message"] for c in checks if c["status"] == "fail"))
        transport = FileExecutionTransport(project, timeout=30)
        planner = OpenAICompatiblePlanner(model=args.model, endpoint=args.endpoint, api_key=args.api_key)
        agent = TaskAgent(transport, planner, max_steps=args.max_steps, task_timeout=args.task_timeout)
        run_id = "Run_" + uuid.uuid4().hex[:12]
        records = []
        output = args.output or project / "Saved/HarnessEvals" / (run_id + ".json")
        output.parent.mkdir(parents=True, exist_ok=True)
        requested = len(suite["cases"]) * args.trials
        for trial in range(args.trials):
            for raw_case in suite["cases"]:
                namespace = "/Game/HarnessAgentEvals/{}/Trial_{}/{}".format(run_id, trial, raw_case["id"])
                case = render(raw_case, namespace)
                started = time.monotonic()
                result = None
                try:
                    with transport.session():
                        if case.get("setup"):
                            preflight = transport.submit(dict(case["setup"], dry_run=True))
                            if not preflight.get("success"):
                                raise RuntimeError("Evaluation setup preflight failed")
                            setup = transport.submit(dict(case["setup"], dry_run=False))
                            if not setup.get("success"):
                                raise RuntimeError("Evaluation setup failed; see " + str(setup.get("audit_path")))
                        result = agent.run(case["task"], read_only=case.get("read_only", False))
                        responses = []
                        if case.get("expected", "success") == "success":
                            responses = [transport.submit({"commands": [criterion["command"]]}) for criterion in case["criteria"]]
                        graded = score(case, result, responses)
                    record = {"id": case["id"], "trial": trial, "namespace": namespace, "task": case["task"],
                              **graded, "agent_result": result, "golden_results": responses, "elapsed_seconds": time.monotonic() - started}
                except (ValueError, RuntimeError, OSError) as exc:
                    record = {"id": case["id"], "trial": trial, "namespace": namespace, "task": case["task"],
                              "success": False, "error": str(exc), "agent_result": result}
                records.append(record)
                summary = {"run_id": run_id, "model": planner.model, "requested": requested, "completed": len(records),
                           "passed": sum(r["success"] for r in records), "pass_rate": sum(r["success"] for r in records) / requested,
                           "cases": records}
                temporary = output.with_suffix(".tmp")
                temporary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                temporary.replace(output)
                print("{} {} trial {}".format("PASS" if record["success"] else "FAIL", case["id"], trial))
                if transport.pending_path.exists():
                    print("Stopped: an unacknowledged request requires review. Remaining cases were not run.")
                    print("Report: " + str(output))
                    return 1
        print("Prompt success rate: {}/{}; report: {}".format(summary["passed"], requested, output))
        return 0 if summary["passed"] == requested else 1
    except (ValueError, RuntimeError, OSError) as exc:
        print("error: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
