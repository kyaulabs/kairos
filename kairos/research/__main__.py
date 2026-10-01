"""Offline research commands; no trading client or credential loading."""

import argparse
import json
from pathlib import Path

from kairos.research.artifacts import Registry
from kairos.research.data import collect
from kairos.research.experiment import run, seal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("collect", "journal", "development", "seal", "final"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, default=Path("research/PLAN.json"))
    parser.add_argument("--dataset", help="Content hash printed by collect")
    parser.add_argument("--development", help="Content hash printed by development")
    args = parser.parse_args()
    if args.command in {"development", "seal", "final"} and not args.dataset:
        parser.error("--dataset is required")
    if args.command in {"seal", "final"} and not args.development:
        parser.error("--development is required")
    registry = Registry(args.root)
    try:
        if args.command == "collect":
            key = collect(registry, json.loads(args.plan.read_text()))
            print(json.dumps({"dataset": key, "manifest": registry.artifacts.get(key)}, indent=2))
        elif args.command == "journal":
            print(json.dumps(registry.entries(), indent=2))
        else:
            plan = json.loads(args.plan.read_text())
            if args.command == "seal":
                key = seal(registry, plan, args.dataset, args.development)
                print(json.dumps({"seal": key}))
            else:
                key = run(registry, plan, args.dataset, args.command, args.development)
                print(json.dumps({"report": key, "result": registry.artifacts.get(key)}, indent=2))
    finally:
        registry.close()


if __name__ == "__main__":
    main()
