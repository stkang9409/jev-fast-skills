"""The complete agent loop with Antigravity Handoff and resume support."""

import base64
import time
from pathlib import Path

from .browser import Browser, StalePage
from .model import action_space, choose, field_context, field_text
from .questions import MAX_STEPS


class Agent:
    def __init__(
        self,
        url=None,
        goals=None,
        *,
        target_id=None,
        session_id=None,
        slots=None,
        initial_text=None,
        record_dir=None,
        screenshots=False,
        background=True,
    ):
        task = goals.strip() if isinstance(goals, str) else "\n".join(goals).strip() if goals else ""
        if not task:
            raise ValueError("Supply a task / goal")
        plan = [task]
        self.pending_text = None
        self.next_text = initial_text
        self.slots = slots or {}
        self.browser = Browser(url=url, target_id=target_id, session_id=session_id, background=background)
        self.record_dir = Path(record_dir) if record_dir else None
        self.screenshots = screenshots or bool(record_dir)
        try:
            page = self.browser.observe(screenshot=self.screenshots)
        except Exception:
            self.browser.close()
            raise
        self.state = dict(
            browser=self.browser,
            goal="\n".join(plan),
            page=page,
            decision=None,
            history=[],
            status="ready",
            plan=plan,
            plan_index=0,
            decisions=[],
            text_calls=[],
            elapsed_ms=0,
            started_at=None,
            record=bool(self.record_dir),
            pending_action=None,
            pending_context=None,
        )
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            if "screenshot" in page:
                (self.record_dir / "000000.jpg").write_bytes(base64.b64decode(page["screenshot"]))

    def snapshot(self):
        return {
            **{k: v for k, v in self.state.items() if k != "browser"},
            "elements": action_space(self.state["page"]["actions"])[0],
            "target_id": self.browser.target,
            "session_id": self.browser.session,
        }

    def command(self, name, body=None):
        body = body or {}
        state = self.state
        if name == "tick":
            try:
                self.command("predict", {})
                return self.command("act", {"fingerprint": state["page"]["fingerprint"]})
            except StalePage:
                state["decision"] = None
                state["status"] = "ready"
                state["page"] = state["browser"].observe(screenshot=self.screenshots)
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                return self.snapshot()
        elif name == "predict":
            if not state["browser"]:
                raise ValueError("Browser not initialized")
            if state["started_at"] is None:
                state["started_at"] = time.perf_counter()
            if not state["browser"].fresh(state["page"]):
                state["page"] = state["browser"].observe(screenshot=self.screenshots)
            state["decision"] = None
            if state["status"] in {"done", "blocked"}:
                raise ValueError("This run has stopped. Start a fresh run.")
            if len(state["decisions"]) >= MAX_STEPS * 2:
                raise ValueError("Reached maximum model-call budget")
            state["decision"] = choose(state["page"], state["goal"], state["history"])
            state["decisions"].append(
                {
                    **state["decision"],
                    "fingerprint": state["page"]["fingerprint"],
                    "elapsed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                }
            )
            state["status"] = "predicted"
        elif name == "act":
            decision, page = state["decision"], state["page"]
            if not decision or body.get("fingerprint") != page["fingerprint"]:
                raise ValueError("Observe and choose before acting")
            state["decision"] = None
            selected = decision["choice"]
            if selected in {"DONE", "BLOCKED"}:
                if not state["browser"].fresh(page):
                    state["status"] = "ready"
                    raise StalePage("Page changed since the decision. Choose again.")
                state["status"] = "done" if selected == "DONE" else "blocked"
                state["plan_index"] = int(selected == "DONE")
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                return self.snapshot()

            action = next(a for a in page["actions"] if a["id"] == selected)
            if len(state["history"]) >= MAX_STEPS:
                state["status"] = "blocked"
                raise ValueError(f"Stopped at {MAX_STEPS} action budget")

            text, helper = None, None
            if action["kind"] == "fill":
                if not state["browser"].fresh(page, action):
                    raise StalePage("Page changed before text input. Choose again.")
                context = field_context(state["goal"], action, page, state["history"])

                # Check direct text or slots
                text, helper = field_text(context, slots=self.slots, direct_text=self.next_text)
                self.next_text = None

                if text is None:
                    # Signal Antigravity handoff needed!
                    state["status"] = "need_text"
                    state["pending_action"] = action
                    state["pending_context"] = context
                    state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                    return self.snapshot()

                state["text_calls"].append({**(helper or {}), "field": action["label"], "value": text})

            state["browser"].act(action, page, text=text)
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            state["history"].append(
                {
                    "step": len(state["history"]) + 1,
                    "action": action["label"],
                    "kind": action["kind"],
                    "choice": selected,
                    "probability": decision["probabilities"].get(selected, 1.0),
                    "confidence": decision["confidence"],
                    "latency_ms": decision["latency_ms"],
                    "text": text,
                    "text_helper": helper["model"] if helper else None,
                    "text_latency_ms": helper["latency_ms"] if helper else 0,
                    "operation": decision["operation"],
                    "target": decision["target"],
                    "page_changed": None,
                    "url": page["url"],
                    "usage": decision["usage"],
                    "executed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                    "elapsed_ms": state["elapsed_ms"],
                }
            )
            state["page"] = state["browser"].observe(screenshot=self.screenshots)
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            state["history"][-1].update(
                page_changed=state["page"]["fingerprint"] != page["fingerprint"],
                url=state["page"]["url"],
                elapsed_ms=state["elapsed_ms"],
            )
            if state["record"] and "screenshot" in state["page"]:
                (self.record_dir / f"{state['elapsed_ms']:06d}.jpg").write_bytes(
                    base64.b64decode(state["page"]["screenshot"])
                )
            repeated = state["history"][-3:]
            state["status"] = (
                "blocked"
                if len(repeated) == 3 and all(h["page_changed"] is False and h["kind"] != "wait" for h in repeated)
                else "ready"
            )
        else:
            raise ValueError(f"Unknown command: {name}")
        return self.snapshot()

    def run(self):
        while self.state["status"] not in {"done", "blocked", "need_text"}:
            yield self.command("tick")

    def resume_with_text(self, text):
        """Resume execution when in need_text status by supplying the text for the pending action."""
        if self.state["started_at"] is None:
            self.state["started_at"] = time.perf_counter()
        if self.state["status"] != "need_text" or not self.state.get("pending_action"):
            raise ValueError("Agent is not currently waiting for text.")
        pending = self.state["pending_action"]
        page = self.state["page"]
        helper = {"model": "antigravity_resume", "latency_ms": 0}

        # Resolve the pending action in the current fresh observation
        matching_action = next((a for a in page.get("actions", []) if a["id"] == pending["id"]), None)
        if not matching_action:
            matching_action = next((a for a in page.get("actions", []) if a.get("label") == pending.get("label") and a.get("kind") == "fill"), None)
        action = matching_action or pending

        self.state["browser"].act(action, page, text=text)
        self.state["text_calls"].append({**helper, "field": action["label"], "value": text})
        self.state["elapsed_ms"] = round((time.perf_counter() - self.state["started_at"]) * 1000)
        self.state["history"].append(
            {
                "step": len(self.state["history"]) + 1,
                "action": action["label"],
                "kind": action["kind"],
                "choice": action["id"],
                "probability": 1.0,
                "confidence": 1.0,
                "latency_ms": 0,
                "text": text,
                "text_helper": "antigravity_resume",
                "text_latency_ms": 0,
                "operation": "TYPE_TEXT",
                "target": action["node"],
                "page_changed": None,
                "url": page["url"],
                "usage": {},
                "executed_ms": round((time.perf_counter() - self.state["started_at"]) * 1000),
                "elapsed_ms": self.state["elapsed_ms"],
            }
        )
        self.state["pending_action"] = None
        self.state["pending_context"] = None
        self.state["page"] = self.state["browser"].observe(screenshot=self.screenshots)
        self.state["status"] = "ready"

        # Resume run loop
        return self.run()

    def close(self):
        if hasattr(self, "browser") and self.browser:
            self.browser.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        # Do not close target if waiting for text to allow resumption
        if self.state["status"] != "need_text":
            self.close()
