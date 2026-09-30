"""Offline research commands; no trading client or credential loading."""

import argparse
import json
from pathlib import Path

from kairos.research.artifacts import Registry
from kairos.research.data import collect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("collect", "journal"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, default=Path("research/PLAN.json"))
    args = parser.parse_args()
    registry = Registry(args.root)
    try:
        if args.command == "collect":
            key = collect(registry, json.loads(args.plan.read_text()))
            print(json.dumps({"dataset": key, "manifest": registry.artifacts.get(key)}, indent=2))
        else:
            print(json.dumps(registry.entries(), indent=2))
    finally:
        registry.close()


if __name__ == "__main__":
    main()
