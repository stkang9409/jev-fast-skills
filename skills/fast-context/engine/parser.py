"""AST and signature parser for Python, TS/JS/CJS (including Electron IPC), and Markdown docs."""

import ast
import re
import sys
from pathlib import Path
from typing import List, Dict, Any

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

try:
    from core.shm import RequestMemory
    _SHM = RequestMemory()
except Exception:
    _SHM = None

CACHE_STATS = {"hits": 0, "misses": 0}


class CodeBlock:
    def __init__(
        self,
        file_path: Path,
        name: str,
        kind: str,
        start_line: int,
        end_line: int,
        signature: str,
        content: str,
        summary: str = "",
    ):
        self.file_path = file_path
        self.name = name
        self.kind = kind
        self.start_line = start_line
        self.end_line = end_line
        self.signature = signature
        self.content = content
        self.summary = summary

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file": str(self.file_path),
            "name": self.name,
            "kind": self.kind,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "signature": self.signature,
            "content": self.content,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CodeBlock":
        return cls(
            file_path=Path(d["file"]),
            name=d["name"],
            kind=d["kind"],
            start_line=int(d["start_line"]),
            end_line=int(d["end_line"]),
            signature=d["signature"],
            content=d.get("content", ""),
            summary=d.get("summary", ""),
        )


def parse_file(file_path: Path, max_lines_full: int = 18) -> List[CodeBlock]:
    """Parse a source file into semantic symbols and code blocks (backed by RAM AST cache)."""
    if not file_path.is_file():
        return []

    abs_str = str(file_path.resolve())
    cache_key = f"ctx:ast:v4:{abs_str}"
    try:
        st = file_path.stat()
        mtime_ns = st.st_mtime_ns
        fsize = st.st_size
    except Exception:
        mtime_ns, fsize = 0, 0

    if _SHM is not None and mtime_ns > 0:
        cached = _SHM.get(cache_key)
        if (
            isinstance(cached, dict)
            and cached.get("mtime_ns") == mtime_ns
            and cached.get("size") == fsize
        ):
            CACHE_STATS["hits"] += 1
            return [CodeBlock.from_dict(b) for b in cached.get("blocks", [])]

    CACHE_STATS["misses"] += 1

    try:
        raw_text = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    lines = raw_text.splitlines(keepends=True)
    total_lines = len(lines)
    if total_lines == 0:
        return []

    if total_lines <= max_lines_full:
        blocks = [
            CodeBlock(
                file_path=file_path,
                name="full_file",
                kind="file",
                start_line=1,
                end_line=total_lines,
                signature=f"File: {file_path.name} (full)",
                content=raw_text,
                summary=raw_text.strip()[:150].replace("\n", " "),
            )
        ]
    else:
        suffix = file_path.suffix.lower()
        if suffix == ".py":
            blocks = _parse_python(file_path, raw_text, lines)
        elif suffix in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".cts", ".mts"}:
            blocks = _parse_js_ts(file_path, raw_text, lines)
        elif suffix in {".md", ".mdx"}:
            blocks = _parse_markdown(file_path, lines)
        else:
            blocks = _parse_generic(file_path, raw_text, lines)

    if _SHM is not None and mtime_ns > 0 and blocks:
        try:
            _SHM.set(
                cache_key,
                {
                    "mtime_ns": mtime_ns,
                    "size": fsize,
                    "blocks": [b.to_dict() for b in blocks],
                },
            )
        except Exception:
            pass

    return blocks


def _parse_python(file_path: Path, raw_text: str, lines: List[str]) -> List[CodeBlock]:
    try:
        tree = ast.parse(raw_text)
    except SyntaxError:
        return _parse_generic(file_path, raw_text, lines)

    blocks = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            s_line = node.lineno
            e_line = node.end_lineno or s_line
            doc = ast.get_docstring(node) or ""
            sig = f"def {node.name}(...)"
            content = "".join(lines[s_line - 1 : e_line])
            blocks.append(
                CodeBlock(
                    file_path=file_path,
                    name=node.name,
                    kind="function",
                    start_line=s_line,
                    end_line=e_line,
                    signature=sig,
                    content=content,
                    summary=doc.strip()[:120] if doc else content.strip()[:100].replace("\n", " "),
                )
            )
        elif isinstance(node, ast.ClassDef):
            c_s_line = node.lineno
            c_e_line = node.end_lineno or c_s_line
            c_doc = ast.get_docstring(node) or ""
            method_found = False
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method_found = True
                    m_s_line = sub.lineno
                    m_e_line = sub.end_lineno or m_s_line
                    m_doc = ast.get_docstring(sub) or ""
                    m_name = f"{node.name}.{sub.name}"
                    m_content = "".join(lines[m_s_line - 1 : m_e_line])
                    blocks.append(
                        CodeBlock(
                            file_path=file_path,
                            name=m_name,
                            kind="method",
                            start_line=m_s_line,
                            end_line=m_e_line,
                            signature=f"def {m_name}(...)",
                            content=m_content,
                            summary=m_doc.strip()[:120] if m_doc else m_content.strip()[:100].replace("\n", " "),
                        )
                    )
            if not method_found or (c_e_line - c_s_line) < 40:
                c_content = "".join(lines[c_s_line - 1 : c_e_line])
                blocks.append(
                    CodeBlock(
                        file_path=file_path,
                        name=node.name,
                        kind="class",
                        start_line=c_s_line,
                        end_line=c_e_line,
                        signature=f"class {node.name}",
                        content=c_content,
                        summary=c_doc.strip()[:120] if c_doc else c_content.strip()[:100].replace("\n", " "),
                    )
                )

    if not blocks:
        return _parse_generic(file_path, raw_text, lines)
    return blocks


def _find_brace_end_line(lines: List[str], start_line: int, max_span: int = 85) -> int:
    """Find the closing line of a JS/TS/CJS block (handling destructured params `({ ... }) {` properly)."""
    brace_depth = 0
    paren_depth = 0
    body_opened = False
    limit = min(len(lines), start_line - 1 + max_span)
    for idx in range(start_line - 1, limit):
        line = lines[idx]
        clean = re.sub(r"//.*$", "", line)
        for ch in clean:
            if ch == "(":
                paren_depth += 1
            elif ch == ")":
                paren_depth = max(0, paren_depth - 1)
            elif ch == "{":
                brace_depth += 1
                if paren_depth == 0:
                    body_opened = True
            elif ch == "}":
                brace_depth -= 1
        if body_opened and brace_depth <= 0 and paren_depth == 0:
            return idx + 1
        if not body_opened and paren_depth == 0 and clean.rstrip().endswith(";") and idx >= start_line - 1:
            return idx + 1
    return min(len(lines), start_line + 45)


def _parse_js_ts(file_path: Path, raw_text: str, lines: List[str]) -> List[CodeBlock]:
    """Parse ESModule, TypeScript, CommonJS (.cjs), and Electron IPC/Preload symbols."""
    blocks = []
    seen_starts = set()

    # 1. Standard & CommonJS declarations: function, class, interface, type, const/let, exports.X
    decl_pattern = re.compile(
        r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?(function|class|interface|type)\s+([a-zA-Z0-9_$]+)"
        r"|^(?:export\s+)?(const|let)\s+([a-zA-Z0-9_$]+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[a-zA-Z0-9_$]+)\s*=>"
        r"|^\s*(?:module\.)?exports\.([a-zA-Z0-9_$]+)\s*=\s*(?:async\s*)?(?:function|\([^)]*\)\s*=>)"
        r"|^  (?:async\s+)?([a-zA-Z0-9_$]+)\s*\([^)]*\)\s*\{",
        re.MULTILINE,
    )

    for match in decl_pattern.finditer(raw_text):
        start_line = raw_text[:match.start()].count("\n") + 1
        if start_line in seen_starts:
            continue
        kw = match.group(1) or match.group(3) or "function"
        name = match.group(2) or match.group(4) or match.group(5) or match.group(6)
        if not name or name in {"if", "for", "while", "switch", "catch", "return"}:
            continue
        seen_starts.add(start_line)
        end_line = _find_brace_end_line(lines, start_line)
        content = "".join(lines[start_line - 1 : end_line])
        kind = "class" if kw == "class" else ("type" if kw in {"interface", "type"} else "function")
        blocks.append(
            CodeBlock(
                file_path=file_path,
                name=name,
                kind=kind,
                start_line=start_line,
                end_line=end_line,
                signature=f"{kw} {name}",
                content=content,
                summary=content.strip()[:120].replace("\n", " "),
            )
        )

    # 2. Electron IPC channels (ipcMain.handle/on, ipcRenderer.invoke/send, contextBridge.exposeInMainWorld)
    ipc_pattern = re.compile(
        r"(ipcMain\.(?:handle|on)|handleIpc|ipcRenderer\.(?:invoke|send)|contextBridge\.exposeInMainWorld)\s*\(\s*['\"]([^'\"]+)['\"]",
        re.MULTILINE,
    )
    for match in ipc_pattern.finditer(raw_text):
        start_line = raw_text[:match.start()].count("\n") + 1
        if start_line in seen_starts:
            continue
        seen_starts.add(start_line)
        api_call, channel = match.group(1), match.group(2)
        end_line = _find_brace_end_line(lines, start_line, max_span=50)
        content = "".join(lines[start_line - 1 : end_line])
        sym_name = f"{api_call}('{channel}')"
        blocks.append(
            CodeBlock(
                file_path=file_path,
                name=sym_name,
                kind="ipc",
                start_line=start_line,
                end_line=end_line,
                signature=sym_name,
                content=content,
                summary=content.strip()[:120].replace("\n", " "),
            )
        )

    blocks.sort(key=lambda b: b.start_line)
    if not blocks:
        return _parse_generic(file_path, raw_text, lines)
    return blocks


def _parse_markdown(file_path: Path, lines: List[str]) -> List[CodeBlock]:
    """Parse Markdown manuals/docs by heading sections (#, ##, ###) instead of blind 50-line chunks."""
    blocks = []
    heading_indices = []
    for idx, line in enumerate(lines):
        m = re.match(r"^(#{1,3})\s+(.+)", line.strip())
        if m:
            heading_indices.append((idx + 1, m.group(2).strip()))

    if not heading_indices:
        return _parse_generic(file_path, "".join(lines), lines)

    for i, (start_line, title) in enumerate(heading_indices):
        next_start = heading_indices[i + 1][0] - 1 if i + 1 < len(heading_indices) else len(lines)
        end_line = min(next_start, start_line + 65)
        content = "".join(lines[start_line - 1 : end_line])
        blocks.append(
            CodeBlock(
                file_path=file_path,
                name=f"doc:{title[:45]}",
                kind="doc_section",
                start_line=start_line,
                end_line=end_line,
                signature=f"Section: {title[:60]}",
                content=content,
                summary=content.strip()[:130].replace("\n", " "),
            )
        )
    return blocks


def _parse_generic(file_path: Path, raw_text: str, lines: List[str], chunk_size: int = 50) -> List[CodeBlock]:
    blocks = []
    total = len(lines)
    for idx, i in enumerate(range(0, total, chunk_size)):
        chunk_lines = lines[i : i + chunk_size]
        start_line = i + 1
        end_line = min(total, i + chunk_size)
        content = "".join(chunk_lines)
        name = f"section_{idx + 1}"
        blocks.append(
            CodeBlock(
                file_path=file_path,
                name=name,
                kind="section",
                start_line=start_line,
                end_line=end_line,
                signature=f"{file_path.name}:{start_line}-{end_line}",
                content=content,
                summary=content.strip()[:120].replace("\n", " "),
            )
        )
    return blocks
