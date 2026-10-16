#!/usr/bin/env python3
"""CLI runner for TypeSafe Jev Fast Browser with Antigravity Handoff support."""

import argparse
import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Ensure bold-bell root and skills directory are on sys.path
root_dir = Path(__file__).resolve().parents[3]
load_dotenv(dotenv_path=root_dir / ".env")
sys.path.insert(0, str(root_dir))

from skills.fast_browser.engine.agent import Agent

DEFAULT_SESSION_FILE = Path("/tmp/fast_browser_session.json")


def save_session(agent, session_file):
    state = agent.state
    action = state.get("pending_action")
    context = state.get("pending_context")
    data = {
        "target_id": agent.browser.target,
        "session_id": agent.browser.session,
        "goal": state["goal"],
        "plan": state["plan"],
        "url": state["page"]["url"],
        "history": state["history"],
        "pending_action": action,
        "pending_context": context,
        "slots": agent.slots,
    }
    session_file.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def load_session(session_file):
    if not session_file.exists():
        raise FileNotFoundError(f"Session file {session_file} does not exist.")
    return json.loads(session_file.read_text())


def main():
    parser = argparse.ArgumentParser(description="Fast browser agent powered by TypeSafe Jev.")
    parser.add_argument("--url", help="Target URL to navigate to")
    parser.add_argument("--target-id", help="Target ID of an existing tab to attach to")
    parser.add_argument("--goal", help="The natural-language goal for the agent")
    parser.add_argument("--slots", help="JSON string of pre-filled slots / key-values")
    parser.add_argument("--text", help="Direct text to type into the current/next field")
    parser.add_argument("--session-file", default=str(DEFAULT_SESSION_FILE), help="Path to session file")
    parser.add_argument("--resume", action="store_true", help="Resume from previous session waiting for text")
    parser.add_argument("--keep-open", action="store_true", help="Keep browser tab open after completion")
    parser.add_argument("--foreground", action="store_true", help="Open tab in foreground (default: background / silent)")

    args = parser.parse_args()
    session_file = Path(args.session_file)

    slots_dict = {}
    if args.slots:
        try:
            slots_dict = json.loads(args.slots)
        except json.JSONDecodeError:
            print(json.dumps({"status": "ERROR", "message": "Invalid JSON in --slots"}), file=sys.stderr)
            sys.exit(1)

    if args.resume:
        session_data = load_session(session_file)
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

        text_to_inject = args.text
        if not text_to_inject:
            print(json.dumps({"status": "ERROR", "message": "--resume requires --text"}), file=sys.stderr)
            sys.exit(1)

        for step in agent.resume_with_text(text_to_inject):
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

        for step in agent.run():
            pass

    status = agent.state["status"]

    if status == "need_text":
        save_session(agent, session_file)
        action = agent.state.get("pending_action") or {}
        context = agent.state.get("pending_context") or {}
        output = {
            "status": "NEED_TEXT",
            "session_file": str(session_file),
            "target_id": agent.browser.target,
            "field_label": action.get("label", "Unknown field"),
            "field_role": action.get("role", "input"),
            "current_url": agent.state["page"]["url"],
            "page_title": agent.state["page"]["title"],
            "context": context,
            "instruction": "Antigravity가 대화 맥락에 맞는 텍스트를 작성한 뒤 python scripts/fast_browse.py --resume --text '<작성된 텍스트>'로 실행하세요.",
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        sys.exit(0)

    elif status == "done":
        if session_file.exists():
            session_file.unlink(missing_ok=True)
        page_text = ""
        try:
            page_text = (agent.browser.evaluate("document.body.innerText") or "").strip()[:8000]
        except Exception:
            pass
        output = {
            "status": "DONE",
            "elapsed_ms": agent.state["elapsed_ms"],
            "final_url": agent.state["page"]["url"],
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
        output = {
            "status": status.upper(),
            "elapsed_ms": agent.state["elapsed_ms"],
            "final_url": agent.state["page"]["url"],
            "actions_count": len(agent.state["history"]),
            "reason": "Execution ended without reaching DONE (blocked or budget exceeded).",
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        if not args.keep_open:
            agent.close()
        else:
            agent.browser.reveal()
        sys.exit(1)


if __name__ == "__main__":
    main()
