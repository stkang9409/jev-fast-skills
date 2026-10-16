#!/usr/bin/env python3
"""Antigravity Stop Lifecycle Hook: Automatically releases fast-memory POSIX RAM block."""

import json
import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parents[3]
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from skills.fast_memory.engine.shm_manager import RequestMemory


def main():
    try:
        RequestMemory.teardown_all()
    except Exception:
        pass
    print(json.dumps({"decision": "stop"}))


if __name__ == "__main__":
    main()
