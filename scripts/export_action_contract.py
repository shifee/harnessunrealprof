#!/usr/bin/env python3
"""Generate the documented JSON action catalog from the shared contract."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from unreal_harness.action_contract import CONTRACT_VERSION, describe_contract


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = ROOT / "docs/action-contract.json"
    text = json.dumps({"contract_version": CONTRACT_VERSION, "actions": describe_contract()}, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            print("Action catalog is stale. Run py scripts/export_action_contract.py", file=sys.stderr)
            return 1
    else:
        path.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
