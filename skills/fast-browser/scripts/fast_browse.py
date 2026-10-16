#!/usr/bin/env python3
"""CLI runner for TypeSafe Jev Fast Browser with RAM-backed Handoff & Auth Detection."""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from dotenv import load_dotenv

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(dotenv_path=PLUGIN_ROOT / ".env")
load_dotenv(dotenv_path=Path.home() / ".gemini" / ".env")
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from skills.fast_browser.engine.agent import Agent
from core.shm import RequestMemory

RAM_SESSION_KEY = "browser:handoff:latest"


def save_session_ram(agent):
    state = agent.state
    data = {
        "target_id": agent.browser.target,
        "session_id": agent.browser.session,
        "goal": state["goal"],
        "plan": state["plan"],
        "url": state["page"]["url"],
        "history": state["history"],
        "pending_action": state.get("pending_action"),
        "pending_context": state.get("pending_context"),
        "slots": agent.slots,
    }
    shm = RequestMemory()
    shm.set(RAM_SESSION_KEY, data)
    shm.set(f"browser:handoff:{agent.browser.target}", data)


def load_session_ram(target_id=None):
    shm = RequestMemory()
    key = f"browser:handoff:{target_id}" if target_id else RAM_SESSION_KEY
    data = shm.get(key)
    if not data:
        raise RuntimeError(f"No active browser handoff session found in RAM ({key}).")
    return data


def clear_session_ram(target_id=None):
    try:
        shm = RequestMemory()
        shm.delete(RAM_SESSION_KEY)
        if target_id:
            shm.delete(f"browser:handoff:{target_id}")
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="Fast browser agent powered by TypeSafe Jev.")
    parser.add_argument("--url", help="Target URL to navigate to")
    parser.add_argument("--target-id", help="Target ID of an existing tab to attach to")
    parser.add_argument("--goal", help="The natural-language goal for the agent")
    parser.add_argument("--slots", help="JSON string of pre-filled slots / key-values")
    parser.add_argument("--text", help="Direct text to type into the current/next field")
    parser.add_argument("--resume", action="store_true", help="Resume from previous RAM session waiting for text")
    parser.add_argument("--keep-open", action="store_true", help="Keep browser tab open after completion")
    parser.add_argument("--foreground", action="store_true", help="Open tab in foreground (default: background / silent)")

    args = parser.parse_args()

    slots_dict = {}
    if args.slots:
        try:
            slots_dict = json.loads(args.slots)
        except json.JSONDecodeError:
            print(json.dumps({"status": "ERROR", "message": "Invalid JSON in --slots"}), file=sys.stderr)
            sys.exit(1)

    if args.resume:
        session_data = load_session_ram(args.target_id)
        target_id = session_data["target_id"]
        session_id = session_data["session_id"]
        goal = session_data["goal"]
        slots = session_data.get("slots", slots_dict)

        agent = Agent(
            goals=goal,
            target_id=target_id,
            session_id=session_id,
            slots=slots,
            background=not args.foreground,
        )
        agent.state["history"] = session_data.get("history", [])
        agent.state["pending_action"] = session_data.get("pending_action")
        agent.state["pending_context"] = session_data.get("pending_context")
        agent.state["status"] = "need_text"

        if not args.text:
            print(json.dumps({"status": "ERROR", "message": "--resume requires --text"}), file=sys.stderr)
            sys.exit(1)

        for _ in agent.resume_with_text(args.text):
            pass
    else:
        if not args.target_id and (not args.url or not args.goal):
            print(json.dumps({"status": "ERROR", "message": "--url (or --target-id) and --goal are required"}), file=sys.stderr)
            sys.exit(1)

        agent = Agent(
            url=args.url,
            goals=args.goal,
            target_id=args.target_id,
            slots=slots_dict,
            initial_text=args.text,
            background=not args.foreground,
        )
        for _ in agent.run():
            pass

    status = agent.state["status"]
    final_url = agent.state["page"]["url"]

    if status == "need_text":
        save_session_ram(agent)
        action = agent.state.get("pending_action") or {}
        context = agent.state.get("pending_context") or {}
        output = {
            "status": "NEED_TEXT",
            "storage": "POSIX_SHM_RAM",
            "target_id": agent.browser.target,
            "field_label": action.get("label", "Unknown field"),
            "field_role": action.get("role", "input"),
            "current_url": final_url,
            "page_title": agent.state["page"]["title"],
            "context": context,
            "instruction": "맥락에 맞는 텍스트를 작성한 뒤 --resume --text '<작성된 텍스트>'로 재실행하세요.",
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        sys.exit(0)

    elif status == "done":
        clear_session_ram(agent.browser.target)
        page_text = ""
        try:
            page_text = (agent.browser.evaluate("document.body.innerText") or "").strip()[:8000]
        except Exception:
            pass
        output = {
            "status": "DONE",
            "elapsed_ms": agent.state["elapsed_ms"],
            "final_url": final_url,
            "page_title": agent.state["page"]["title"],
            "page_text": page_text,
            "actions_count": len(agent.state["history"]),
            "history": [
                {
                    "step": h["step"],
                    "action": h["action"],
                    "kind": h["kind"],
                    "text": h.get("text"),
                    "latency_ms": h.get("latency_ms", 0),
                }
                for h in agent.state["history"]
            ],
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        if not args.keep_open:
            agent.close()
        else:
            agent.browser.reveal()
        sys.exit(0)

    else:
        is_auth_wall = bool(re.search(r"(/login|/signin|/auth|oauth|accounts\.google\.com|sso)", final_url, re.IGNORECASE))
        out_status = "AUTH_REQUIRED" if is_auth_wall else status.upper()
        reason = (
            f"Redirected to authentication/login wall ({final_url}). Use authenticated CLI (e.g. gh) or ask user to sign in."
            if is_auth_wall
            else "Execution ended without reaching DONE (blocked or step budget exceeded)."
        )
        output = {
            "status": out_status,
            "elapsed_ms": agent.state["elapsed_ms"],
            "final_url": final_url,
            "actions_count": len(agent.state["history"]),
            "reason": reason,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        if not args.keep_open:
            agent.close()
        else:
            agent.browser.reveal()
        sys.exit(1)


if __name__ == "__main__":
    main()
