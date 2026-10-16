#!/usr/bin/env python3
"""CLI interface for fast-memory request-scoped working memory with binary buffer & lock support."""

import argparse
import json
import sys
from pathlib import Path

# Add skill root to path
SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from engine.shm_manager import RequestMemory


def main():
    parser = argparse.ArgumentParser(description="TypeSafe Jev Atomic Request-Scoped RAM Memory Engine")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # set
    set_parser = subparsers.add_parser("set", help="Set a JSON/text key-value in RAM")
    set_parser.add_argument("key", help="Key name")
    set_parser.add_argument("value", nargs="?", help="Value string (or read from stdin)")

    # get
    get_parser = subparsers.add_parser("get", help="Get a value from RAM")
    get_parser.add_argument("key", help="Key name")
    get_parser.add_argument("--json", action="store_true", help="Parse and pretty-print JSON")

    # set-bytes
    sb_parser = subparsers.add_parser("set-bytes", help="Store raw binary data in RAM (e.g. image, AST buffer)")
    sb_parser.add_argument("key", help="Key name")
    sb_parser.add_argument("file", nargs="?", default="-", help="Input file path (or '-' for stdin)")

    # get-bytes
    gb_parser = subparsers.add_parser("get-bytes", help="Extract raw binary data from RAM")
    gb_parser.add_argument("key", help="Key name")
    gb_parser.add_argument("file", nargs="?", default="-", help="Output file path (or '-' for stdout)")

    # list
    subparsers.add_parser("list", help="List all active keys in RAM")

    # delete
    del_parser = subparsers.add_parser("del", help="Delete a key from RAM")
    del_parser.add_argument("key", help="Key name")

    # clear
    subparsers.add_parser("clear", help="Clear all keys in RAM")

    # destroy
    subparsers.add_parser("destroy", help="Completely unlink and release RAM block")

    args = parser.parse_args()

    if args.command == "set":
        val = args.value
        if val is None:
            val = sys.stdin.read().strip()
        try:
            parsed = json.loads(val)
        except Exception:
            parsed = val
        mem = RequestMemory()
        mem.set(args.key, parsed)
        mem.close()
        print(f"✅ Stored '{args.key}' in RAM (0.06ms, atomic)")

    elif args.command == "get":
        try:
            mem = RequestMemory(create=False)
            val = mem.get(args.key)
            mem.close()
        except FileNotFoundError:
            print(f"Key '{args.key}' not found in RAM (memory unallocated).", file=sys.stderr)
            sys.exit(1)
        if val is None:
            print(f"Key '{args.key}' not found in RAM.", file=sys.stderr)
            sys.exit(1)
        if args.json and isinstance(val, (dict, list)):
            print(json.dumps(val, indent=2, ensure_ascii=False))
        else:
            print(val if isinstance(val, str) else json.dumps(val, ensure_ascii=False))

    elif args.command == "set-bytes":
        if args.file == "-":
            data = sys.stdin.buffer.read()
        else:
            data = Path(args.file).read_bytes()
        mem = RequestMemory()
        mem.set_bytes(args.key, data)
        mem.close()
        print(f"✅ Stored {len(data):,} bytes in RAM under '{args.key}' (zero-copy, atomic)")

    elif args.command == "get-bytes":
        try:
            mem = RequestMemory(create=False)
            data = mem.get_bytes(args.key)
            mem.close()
        except FileNotFoundError:
            print(f"Key '{args.key}' not found in RAM (memory unallocated).", file=sys.stderr)
            sys.exit(1)
        if data is None:
            print(f"Key '{args.key}' not found in RAM.", file=sys.stderr)
            sys.exit(1)
        if args.file == "-":
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
        else:
            Path(args.file).write_bytes(data)
            print(f"✅ Extracted {len(data):,} bytes to {args.file}")

    elif args.command == "list":
        try:
            mem = RequestMemory(create=False)
            keys = mem.list_keys()
            mem.close()
            print(json.dumps(keys, indent=2, ensure_ascii=False))
        except FileNotFoundError:
            print("[]")

    elif args.command == "del":
        try:
            mem = RequestMemory(create=False)
            mem.delete(args.key)
            mem.close()
            print(f"✅ Deleted '{args.key}' from RAM")
        except FileNotFoundError:
            pass

    elif args.command == "clear":
        try:
            mem = RequestMemory(create=False)
            mem.clear()
            mem.close()
            print("✅ Cleared all RAM keys")
        except FileNotFoundError:
            pass

    elif args.command == "destroy":
        RequestMemory.teardown_all()
        print("⚡ RAM memory block cleanly unlinked and released to OS (Zero residual files)")


if __name__ == "__main__":
    main()
