"""Example policies and local logging for the explicitly selected hooks.json."""
import json
import sys
from pathlib import Path


def main():
    payload = json.load(sys.stdin)
    if sys.argv[1] == "guard":
        denied = {"object.call", "backend.operation", "recipe.execute"}
        actions = [c["action"] for c in payload["document"]["commands"] if c["action"] in denied]
        print(json.dumps({"allow": not actions, "reason": "Generic calls are disabled by local policy: " + ", ".join(actions)}))
    elif sys.argv[1] == "log":
        # Keep notifications local; write one record per completed attempt.
        root = Path(payload["project"])
        destination = root / "Saved/HarnessHooks/events.jsonl"
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"event": payload["event"], "task_id": payload["task_id"],
                                     "success": payload["report"]["success"],
                                     "phase": payload["report"]["phase"]}, ensure_ascii=False) + "\n")
        print("{}")
    else:
        raise ValueError("Unknown example hook")


if __name__ == "__main__":
    main()
