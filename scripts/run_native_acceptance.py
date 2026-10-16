#!/usr/bin/env python3
"""Exercise the real Editor transport and agent lifecycle without a model server."""
import argparse
import copy
import json
import sys
import uuid
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_ue_smoke_test import smoke_document
from unreal_harness.task_agent import TaskAgent
from unreal_harness.execution import FileExecutionTransport
from unreal_harness.health import inspect_project
from unreal_harness.hooks import Hooks


class FixturePlanner:
    """Predeclared test steps; this is not a test of natural language inference."""
    def __init__(self, steps, criteria):
        self.steps = deque(copy.deepcopy(steps))
        self.criteria = copy.deepcopy(criteria)

    def next_step(self, task, context):
        if not self.steps:
            raise RuntimeError("Fixture steps exhausted before acceptance passed; inspect failed predicates")
        return self.steps.popleft()

    def prepare_task(self, task, context):
        return {"status": "ready", "goal": task, "criteria": self.criteria}


def action(commands):
    return {"status": "act", "reason": "native acceptance fixture", "commands": commands}


FINISH = {"status": "finish", "reason": "run fixed checks", "commands": []}


def fixture(namespace):
    folder = "/Game/CodexHarnessSmoke/" + namespace
    material, blueprint = folder + "/M_Smoke.M_Smoke", folder + "/BP_Smoke.BP_Smoke"
    commands = [c for c in smoke_document(namespace)["commands"] if c["id"] not in {"capabilities", "spawn", "inspect_actor", "save_assets"}]
    commands.append({"id": "save_selected", "action": "asset.save", "depends_on": ["compile"], "arguments": {"assets": [material, blueprint]}})
    def criterion(ident, act, args, path, op, value):
        return {"id": ident, "description": ident, "command": {"id": ident, "action": act, "arguments": args}, "path": path, "operator": op, "value": value}
    criteria = [
        criterion("metallic", "material.inspect", {"material": material}, "scalar_parameters", "contains", {"name": "Metallic", "value": 1}),
        criterion("roughness", "material.inspect", {"material": material}, "scalar_parameters", "contains", {"name": "Roughness", "value": 0.35}),
        criterion("color", "material.inspect", {"material": material}, "vector_parameters", "contains", {"name": "BaseColor", "value": [0.12, 0.14, 0.17, 1]}),
        criterion("saved_material", "asset.inspect", {"asset": material}, "exists_on_disk", "equals", True),
        criterion("saved_blueprint", "asset.inspect", {"asset": blueprint}, "exists_on_disk", "equals", True),
        criterion("graph_call", "blueprint.graph.inspect", {"blueprint": blueprint, "graph": "EventGraph"}, "nodes", "contains", {"class": "/Script/BlueprintGraph.K2Node_CallFunction"}),
    ]
    research = action([{"id": "research", "action": "content.list", "arguments": {"path": folder}}])
    return research, action(commands), criteria


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    args = parser.parse_args(argv)
    project, checks = inspect_project(args.project)
    if any(c["status"] == "fail" for c in checks):
        raise ValueError("Project preflight failed: " + json.dumps(checks))
    namespace = "Acceptance_" + uuid.uuid4().hex[:12]
    research, mutation, criteria = fixture(namespace)
    transport = FileExecutionTransport(project, timeout=30)
    hooks = Hooks.from_file(ROOT / "examples/hooks.json")
    with transport.session():
        stopped = TaskAgent(transport, FixturePlanner([research, mutation], criteria), max_steps=2, hooks=hooks).run("Native material, components, graph and save fixture")
        if stopped.get("phase") != "budget" or stopped.get("result", {}).get("success") is not True:
            report = {"success": False, "planner": "fixture", "namespace": namespace, "stopped": stopped}
        else:
            resumed = TaskAgent(transport, FixturePlanner([research, FINISH], criteria)).resume(stopped["task_id"])
            reader = action([criteria[0]["command"]])
            followup = TaskAgent(transport, FixturePlanner([reader, FINISH], criteria[:3])).run("Read parameters from the fixture material", read_only=True, session=resumed["task_id"]) if resumed["success"] else None
            report = {"success": bool(resumed["success"] and followup and followup["success"]), "planner": "fixture", "namespace": namespace,
                      "stopped": stopped, "resumed": resumed, "followup": followup}
    destination = project / "Saved/HarnessReadiness" / (namespace + ".json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Native lifecycle acceptance: " + ("PASS" if report["success"] else "FAIL"))
    print("Model inference: not tested (fixture planner)")
    print("Report: " + str(destination))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError) as exc:
        print("error: " + str(exc), file=sys.stderr)
        sys.exit(2)
