#!/usr/bin/env python3
"""Lightweight Stop hook to tear down RequestMemory POSIX shared memory segment."""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.shm import RequestMemory


def main():
    try:
        RequestMemory.teardown_all()
    except Exception:
        pass
    print(json.dumps({"decision": "stop"}))


if __name__ == "__main__":
    main()
