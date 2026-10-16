"""Observed actions through Browser Harness; one CDP session, no per-step subprocess."""

import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from browser_harness.admin import ensure_daemon
from browser_harness.helpers import cdp

# Atomically read visible content and controls, preserving actual DOM node identity.
READ_STATE = Path(__file__).with_name("snapshot.js").read_text()
MARKER = f"(() => {{ const state={READ_STATE}; return state?.marker ?? null; }})()"

class StalePage(ValueError):
    """A decision no longer refers to the observed page."""


class Browser:
    def __init__(self, url=None, target_id=None, session_id=None, new_window=False, reuse_existing=False, background=True):
        ensure_daemon()
        if session_id and target_id:
            self.target = target_id
            self.session = session_id
        elif target_id:
            self.target = target_id
            self.session = cdp("Target.attachToTarget", targetId=self.target, flatten=True)["sessionId"]
        else:
            found_target = None
            if reuse_existing and url:
                try:
                    targets = cdp("Target.getTargets").get("targetInfos", [])
                    for t in targets:
                        if t.get("type") == "page" and t.get("url") and (url == t["url"] or url.rstrip("/") == t["url"].rstrip("/")):
                            found_target = t["targetId"]
                            break
                except Exception:
                    pass

            if found_target:
                self.target = found_target
                self.session = cdp("Target.attachToTarget", targetId=self.target, flatten=True)["sessionId"]
            else:
                if new_window:
                    # Create dedicated off-screen window
                    self.target = cdp("Target.createTarget", url="about:blank", newWindow=True, background=True)["targetId"]
                    self.session = cdp("Target.attachToTarget", targetId=self.target, flatten=True)["sessionId"]
                    try:
                        win = cdp("Browser.getWindowForTarget", targetId=self.target)
                        cdp("Browser.setWindowBounds", windowId=win["windowId"], bounds={"left": -2000, "top": 0, "width": 1280, "height": 800, "windowState": "normal"})
                    except Exception:
                        pass
                else:
                    # Default: add as a new tab to the existing Chrome window (no OS-level focus stealing)
                    self.target = cdp("Target.createTarget", url="about:blank", newWindow=False, background=background)["targetId"]
                    self.session = cdp("Target.attachToTarget", targetId=self.target, flatten=True)["sessionId"]

            try:
                self.call("Page.enable")
                self.call("Emulation.setDeviceMetricsOverride", width=1280, height=800, deviceScaleFactor=1, mobile=False)
                self.call("Emulation.setFocusEmulationEnabled", enabled=True)
            except Exception:
                pass
            if url:
                self.call("Page.navigate", url=url)
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    try:
                        ready = self.call("Runtime.evaluate", expression="document.readyState", returnByValue=True, response_timeout=1.0)
                        if ready.get("result", {}).get("value") == "complete":
                            break
                    except Exception:
                        pass
                    time.sleep(0.05)

    def call(self, method, **params):
        return cdp(method, session_id=self.session, **params)

    def evaluate(self, expression, timeout=2.5):
        try:
            response = self.call("Runtime.evaluate", expression=expression, returnByValue=True, response_timeout=timeout)
        except Exception as e:
            raise StalePage(f"Evaluation failed or timed out: {e}") from e
        if response.get("exceptionDetails"):
            raise StalePage("Document changed during evaluation")
        return response.get("result", {}).get("value")

    def observe(self, screenshot=True):
        if getattr(self, "after_input", None):
            action, self.after_input = self.after_input, None
            # This is read-only and happens after execution was logged, even if navigation interrupts it.
            try:
                self.call(
                    "Runtime.evaluate",
                    expression="""(action => new Promise(resolve => {
                      const field=window.__jevFast?.nodes.get(action.node);
                      const autocomplete=action.kind==='fill' && field?.getAttribute('role')==='combobox';
                      let frames=0, stopped=false;
                      const finish=()=>{ if (!stopped) { stopped=true; resolve(); } };
                      setTimeout(finish, autocomplete ? 100 : 30);
                      const step=()=>{
                        if (stopped) return;
                        const ids=(field?.getAttribute('aria-controls')||field?.getAttribute('aria-owns')||'')
                          .split(/\\s+/).filter(Boolean);
                        const roots=ids.length ? ids.map(id=>document.getElementById(id)).filter(Boolean) : [document];
                        const options=roots.flatMap(root=>[...root.querySelectorAll('[role="option"]')]);
                        if (++frames>=2 && (!autocomplete || options.some(e=>{
                          const r=e.getBoundingClientRect();
                          return r.width && r.height && r.bottom>0 && r.top<innerHeight &&
                            e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
                        }))) finish();
                        else setTimeout(step, 16);
                      };
                      setTimeout(step, 16);
                    }))(""" + json.dumps(action) + ")",
                    awaitPromise=True,
                    returnByValue=True,
                )
            except Exception:
                pass
        for attempt in range(10):
            try:
                return browser_operation(
                    {"operation": "observe", "session": self.session, "screenshot": screenshot}
                )
            except StalePage:
                if attempt == 9:
                    raise
                time.sleep(0.02)
        raise StalePage("Page did not settle")

    def fresh(self, page, action=None):
        if action is not None and action["kind"] in {"click", "select", "fill"}:
            node = action["node"]
            if type(node) is not int:
                return False
            current = self.evaluate(
                "(() => { const c=window.__jevFast; "
                f"return c ? [c.pageKey(),c.guard(c.nodes.get({node}))] : null; }})()"
            )
            return current == [page["page_key"], page["guards"].get(str(node))]
        return self.evaluate(MARKER) == page["marker"]

    def act(self, action, page, text=None):
        if not self.fresh(page, action):
            raise StalePage("Page changed since this decision. Observe again.")
        if action["kind"] == "wait":
            time.sleep(0.1)
        result = browser_operation({"operation": "act", "session": self.session, "action": action, "text": text})
        self.after_input = action if action["kind"] != "wait" else None
        return result

    def reveal(self):
        """Move the offscreen window onto the main screen."""
        if self.target:
            try:
                win = cdp("Browser.getWindowForTarget", targetId=self.target)
                cdp("Browser.setWindowBounds", windowId=win["windowId"], bounds={"left": 100, "top": 100, "width": 1280, "height": 900, "windowState": "normal"})
                cdp("Target.activateTarget", targetId=self.target)
            except Exception:
                pass

    def batch_fetch_texts(self, urls, batch_size=15, timeout=45.0):
        """Fetch multiple same-origin URLs concurrently inside the logged-in browser tab and return structured text."""
        js = f"""
        (async () => {{
          const urls = {json.dumps(list(urls))};
          const batchSize = {int(batch_size)};
          async function getText(url) {{
            const res = await fetch(url, {{ credentials: "include" }});
            const html = await res.text();
            const doc = new DOMParser().parseFromString(html, "text/html");
            doc.querySelectorAll("script, style").forEach(el => el.remove());
            doc.querySelectorAll("br").forEach(el => el.replaceWith("\\n"));
            doc.querySelectorAll("p, div, tr, li, ul, table, h1, h2, h3, h4").forEach(el => el.append("\\n"));
            return {{ url, text: (doc.body ? doc.body.textContent : "").replace(/\\u200b/g, "") }};
          }}
          const out = [];
          for (let i = 0; i < urls.length; i += batchSize) {{
            const batch = urls.slice(i, i + batchSize);
            out.push(...(await Promise.all(batch.map(getText))));
          }}
          return out;
        }})()
        """
        res = self.call("Runtime.evaluate", expression=js, awaitPromise=True, returnByValue=True, response_timeout=timeout)
        return res.get("result", {}).get("value", [])

    def paste_tsv_to_sheet(self, rows, start_cell="A1", chunk_size=4000):
        """Paste a 2D list of rows into Google Sheets starting at start_cell, chunking TSV over IPC to avoid BrokenPipeError."""
        tsv = "\n".join(
            "\t".join(str(c or "").replace("\t", " ").replace("\r", " ").replace("\n", " / ").strip() for c in r)
            for r in rows
        )
        self.call(
            "Runtime.evaluate",
            expression=f"""
            (() => {{
              window.__tsv = "";
              const nb = document.querySelector("#t-name-box");
              if (nb) {{
                nb.focus();
                nb.value = {json.dumps(start_cell)};
                nb.dispatchEvent(new KeyboardEvent("keydown", {{ key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true }}));
              }}
            }})()
            """,
            returnByValue=True,
        )
        time.sleep(0.35)
        for i in range(0, len(tsv), chunk_size):
            chunk = tsv[i : i + chunk_size]
            self.call("Runtime.evaluate", expression=f"window.__tsv += {json.dumps(chunk)}; true;", returnByValue=True)
        self.call(
            "Runtime.evaluate",
            expression="""
            (() => {
              const target = document.querySelector(".cell-input") || document.activeElement || document.body;
              const dt = new DataTransfer();
              dt.setData("text/plain", window.__tsv);
              const ev = new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true });
              target.dispatchEvent(ev);
              window.__tsv = "";
              return true;
            })()
            """,
            returnByValue=True,
        )

    def close(self):
        if self.target:
            cdp("Target.closeTarget", targetId=self.target)
            self.target = None


def fingerprint(state):
    content = {k: state[k] for k in ("url", "text", "actions", "scroll")}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def browser_operation(request):
    operation = request["operation"]
    session = request["session"]

    def call(method, **params):
        return cdp(method, session_id=session, **params)

    def evaluate(expression, timeout=2.5):
        try:
            result = call("Runtime.evaluate", expression=expression, returnByValue=True, response_timeout=timeout)
        except Exception as e:
            raise StalePage(f"Evaluation timed out or failed: {e}") from e
        if result.get("exceptionDetails"):
            if operation == "act" and request["action"]["kind"] == "select":
                raise RuntimeError("Dropdown execution was interrupted; inspect before retrying.")
            raise StalePage("Document changed during evaluation")
        return result.get("result", {}).get("value")

    if operation == "act":
        action = request["action"]
        kind = action["kind"]
        if kind == "scroll":
            call("Input.dispatchMouseEvent", type="mouseWheel", x=550, y=650, deltaX=0, deltaY=action["delta"])
        elif kind != "wait":
            if type(action["node"]) is not int:
                raise ValueError("Invalid observed node")
            # Code-owned node IDs refer to actual observed elements, never model-generated selectors.
            target = evaluate("""(action => {
              const e=window.__jevFast?.nodes.get(action.node);
              if (!e?.isConnected || e.matches(':disabled') || e.closest('[aria-disabled="true"],[inert]') ||
                  !e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})) return null;
              if (action.kind==='fill' && (e.readOnly || e.getAttribute('aria-readonly')==='true')) return null;
              const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2;
              if (!r.width || !r.height || x<0 || y<0 || x>=innerWidth || y>=innerHeight) return null;
              if (!e.contains(document.elementFromPoint(x,y))) return null;
              if (action.kind==='select') {
                if (e.tagName!=='SELECT' || ![...e.options].some(o=>o.value===action.value &&
                    !o.disabled && !o.closest('optgroup[disabled]'))) return null;
                e.value=action.value;
                e.dispatchEvent(new Event('input',{bubbles:true}));
                e.dispatchEvent(new Event('change',{bubbles:true}));
              }
              return {x,y};
            })(""" + json.dumps(action) + ")")
            if target is None:
                if kind == "select":
                    raise RuntimeError("Dropdown execution was not confirmed; inspect before retrying.")
                raise StalePage("Target changed or is covered. Observe again.")
            if kind != "select":
                x, y = target["x"], target["y"]
                for event in ("mousePressed", "mouseReleased"):
                    call("Input.dispatchMouseEvent", type=event, x=x, y=y, button="left", clickCount=1)
                if kind == "fill":
                    call(
                        "Input.dispatchKeyEvent",
                        type="keyDown",
                        key="a",
                        code="KeyA",
                        modifiers=4 if sys.platform == "darwin" else 2,
                        commands=["selectAll"],
                    )
                    call(
                        "Input.dispatchKeyEvent",
                        type="keyUp",
                        key="a",
                        code="KeyA",
                        modifiers=4 if sys.platform == "darwin" else 2,
                    )
                    call("Input.insertText", text=request["text"])
        return {"executed": action["id"]}

    info = evaluate(READ_STATE)
    if info is None:
        raise StalePage("Document is navigating")
    info["fingerprint"] = fingerprint(info)
    if request.get("screenshot", True):
        info["screenshot"] = call("Page.captureScreenshot", format="jpeg", quality=72)["data"]
    return info
