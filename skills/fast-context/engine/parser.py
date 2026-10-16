"""AST and signature parser for code slicing with RAM SharedMemory AST Cache and JSDoc/SQL/Error extraction."""

import ast
import re
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional

CORE_DIR = Path(__file__).resolve().parents[2] / "core"
if str(CORE_DIR.parent) not in sys.path:
    sys.path.insert(0, str(CORE_DIR.parent))

try:
    from core.shm import RequestMemory
    _SHM_AVAILABLE = True
except Exception:
    _SHM_AVAILABLE = False

CACHE_STATS = {"hits": 0, "misses": 0}
PARSER_VERSION = "v7"

TRIVIAL_HELPER_NAMES = {
    "fail", "unique", "aad", "invalid", "withoutReceipts", "sameProfile",
    "identity", "text", "integer", "realDate", "samAn", "samN", "samDecimal",
    "Snapshot", "Statement", "field", "table",
}

SQL_EXTRACT_RE = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE|FROM|JOIN)\s+([a-zA-Z0-9_]+\.[a-zA-Z0-9_]+)\b",
    re.IGNORECASE,
)
ERR_CODE_RE = re.compile(r"\b([A-Z][A-Z0-9_]{5,})\b")
KO_PHRASE_RE = re.compile(r"[가-힣]{2,}(?:\s+[가-힣0-9A-Za-z_-]+){0,3}")


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
    def from_dict(cls, data: Dict[str, Any]) -> "CodeBlock":
        return cls(
            file_path=Path(data["file"]),
            name=data["name"],
            kind=data["kind"],
            start_line=int(data["start_line"]),
            end_line=int(data["end_line"]),
            signature=data["signature"],
            content=data.get("content", ""),
            summary=data.get("summary", ""),
        )


def _extract_leading_comments(lines: List[str], start_line: int, max_lookback: int = 12) -> str:
    """Extract JSDoc or single-line comments preceding `start_line` (looking past a 1-4 line helper if needed)."""
    comment_parts: List[str] = []
    idx = start_line - 2
    steps = 0
    skipped_code_lines = 0
    while idx >= 0 and steps < max_lookback:
        s = lines[idx].strip()
        if not s:
            if comment_parts:
                break
            idx -= 1
            steps += 1
            continue
        if s.startswith(("//", "/*", "*", "*/")):
            cleaned = re.sub(r"^[/\*\s]+|[\*/\s]+$", "", s).strip()
            if cleaned:
                comment_parts.append(cleaned)
            idx -= 1
            steps += 1
        elif not comment_parts and skipped_code_lines < 4:
            skipped_code_lines += 1
            idx -= 1
            steps += 1
        else:
            break
    comment_parts.reverse()
    return " ".join(comment_parts)[:150]


def _build_rich_summary(lines: List[str], start_line: int, content: str) -> str:
    """Build a high-signal summary combining preceding JSDoc comments, SQL tables, error codes, and Korean domain terms."""
    lead_doc = _extract_leading_comments(lines, start_line)
    sql_tables = []
    for m in SQL_EXTRACT_RE.finditer(content):
        t = m.group(1)
        if t not in sql_tables:
            sql_tables.append(t)

    err_codes = []
    for m in ERR_CODE_RE.finditer(content):
        c = m.group(1)
        if ("_" in c) and c not in err_codes and not c.startswith(("SELECT_", "INSERT_", "UPDATE_")):
            err_codes.append(c)

    ko_terms = []
    for m in KO_PHRASE_RE.finditer(content):
        kp = m.group(0)
        if kp not in ko_terms:
            ko_terms.append(kp)
            if len(ko_terms) >= 6:
                break

    parts = []
    if sql_tables:
        parts.append(f"SQL: {', '.join(sql_tables[:4])}")
    if err_codes:
        parts.append(f"Codes: {', '.join(err_codes[:4])}")
    if ko_terms:
        parts.append(f"Terms: {', '.join(ko_terms[:5])}")
    if lead_doc:
        parts.append(f"Doc: {lead_doc}")

    sig_line = content.strip().splitlines()[0] if content.strip() else ""
    parts.append(sig_line[:90])
    return " | ".join(parts)[:280]


def parse_file(file_path: Path, max_lines_full: int = 35) -> List[CodeBlock]:
    """Parse a source file into semantic symbols and code blocks, backed by POSIX SharedMemory cache."""
    if not file_path.is_file():
        return []

    cache_key = None
    mem = None
    if _SHM_AVAILABLE:
        try:
            st = file_path.stat()
            cache_key = f"ast:{PARSER_VERSION}:{int(st.st_mtime)}:{st.st_size}:{str(file_path.resolve())}"
            mem = RequestMemory(name="fast_context_ast_v1", create=True)
            cached = mem.get(cache_key)
            if cached and isinstance(cached, list):
                CACHE_STATS["hits"] += 1
                mem.close()
                return [CodeBlock.from_dict(item) for item in cached]
        except Exception:
            pass

    CACHE_STATS["misses"] += 1
    blocks = _parse_file_uncached(file_path, max_lines_full=max_lines_full)

    if mem and cache_key and blocks:
        try:
            mem.set(cache_key, [b.to_dict() for b in blocks])
        except Exception:
            pass
    if mem:
        try:
            mem.close()
        except Exception:
            pass

    return blocks


def _parse_file_uncached(file_path: Path, max_lines_full: int = 35) -> List[CodeBlock]:
    try:
        raw_text = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    lines = raw_text.splitlines(keepends=True)
    total_lines = len(lines)
    if total_lines == 0:
        return []

    suffix = file_path.suffix.lower()

    if suffix in {".py", ".pyw"}:
        blocks = _parse_python(file_path, raw_text, lines)
        if blocks:
            return blocks
    elif suffix in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".cts", ".mts"}:
        blocks = _parse_js_ts(file_path, raw_text, lines)
        if blocks:
            return blocks
    elif suffix == ".md":
        return _parse_markdown(file_path, lines)

    if total_lines <= max_lines_full:
        return [
            CodeBlock(
                file_path=file_path,
                name="full_file",
                kind="file",
                start_line=1,
                end_line=total_lines,
                signature=f"File: {file_path.name} (full)",
                content=raw_text,
                summary=_build_rich_summary(lines, 1, raw_text),
            )
        ]

    return _parse_generic(file_path, raw_text, lines)


def _parse_python(file_path: Path, raw_text: str, lines: List[str]) -> List[CodeBlock]:
    try:
        tree = ast.parse(raw_text)
    except SyntaxError:
        return _parse_generic(file_path, raw_text, lines)

    blocks: List[CodeBlock] = []

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            s_line = node.lineno
            e_line = getattr(node, "end_lineno", s_line + 15)
            content = "".join(lines[s_line - 1 : min(e_line, s_line + 80)])
            doc = ast.get_docstring(node) or ""
            sig_line = lines[s_line - 1].strip()
            blocks.append(
                CodeBlock(
                    file_path=file_path,
                    name=node.name,
                    kind="function",
                    start_line=s_line,
                    end_line=e_line,
                    signature=sig_line,
                    content=content,
                    summary=doc.strip()[:140] if doc else _build_rich_summary(lines, s_line, content),
                )
            )
        elif isinstance(node, ast.ClassDef):
            c_s_line = node.lineno
            c_e_line = getattr(node, "end_lineno", c_s_line + 25)
            c_doc = ast.get_docstring(node) or ""
            method_found = False
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method_found = True
                    m_s_line = sub.lineno
                    m_e_line = getattr(sub, "end_lineno", m_s_line + 15)
                    m_content = "".join(lines[m_s_line - 1 : min(m_e_line, m_s_line + 80)])
                    m_doc = ast.get_docstring(sub) or ""
                    m_sig = f"class {node.name} -> {lines[m_s_line - 1].strip()}"
                    blocks.append(
                        CodeBlock(
                            file_path=file_path,
                            name=f"{node.name}.{sub.name}",
                            kind="method",
                            start_line=m_s_line,
                            end_line=m_e_line,
                            signature=m_sig,
                            content=m_content,
                            summary=m_doc.strip()[:140] if m_doc else _build_rich_summary(lines, m_s_line, m_content),
                        )
                    )
            if not method_found or (c_e_line - c_s_line) < 40:
                c_content = "".join(lines[c_s_line - 1 : min(c_e_line, c_s_line + 80)])
                blocks.append(
                    CodeBlock(
                        file_path=file_path,
                        name=node.name,
                        kind="class",
                        start_line=c_s_line,
                        end_line=c_e_line,
                        signature=f"class {node.name}",
                        content=c_content,
                        summary=c_doc.strip()[:140] if c_doc else _build_rich_summary(lines, c_s_line, c_content),
                    )
                )

    if not blocks:
        return _parse_generic(file_path, raw_text, lines)
    return blocks


_STRING_COMMENT_RE = re.compile(
    r"//.*$|/\*[\s\S]*?\*/|'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`"
)


def _find_brace_end_line(lines: List[str], start_line: int, max_span: int = 500) -> int:
    """
    Find the exact closing line of a JS/TS/CJS function, method, or component.
    Properly ignores braces inside parameter lists `({ ... })`, TypeScript return type annotations,
    and string/comment literals so `L70-L70` never happens.
    """
    brace_depth = 0
    paren_depth = 0
    seen_open_paren = False
    params_closed = False
    body_opened = False
    first_line_text = lines[start_line - 1].lstrip()
    is_func_like = bool(
        re.match(
            r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\b"
            r"|^(?:async\s+)?[a-zA-Z0-9_$]+\s*\(",
            first_line_text,
        )
    )

    limit = min(len(lines), start_line - 1 + max_span)
    for idx in range(start_line - 1, limit):
        raw_line = lines[idx]
        clean = _STRING_COMMENT_RE.sub("", raw_line)
        for ch in clean:
            if ch == "(":
                paren_depth += 1
                seen_open_paren = True
            elif ch == ")":
                paren_depth = max(0, paren_depth - 1)
                if seen_open_paren and paren_depth == 0:
                    params_closed = True
            elif ch == "{":
                if is_func_like and not params_closed:
                    # Inside parameter destructuring or inline param type annotation `({ a }: { a: string })`
                    continue
                brace_depth += 1
                if paren_depth == 0:
                    body_opened = True
            elif ch == "}":
                if is_func_like and not params_closed:
                    continue
                brace_depth = max(0, brace_depth - 1)

        if body_opened and brace_depth == 0 and paren_depth == 0:
            # Guard against TS inline return type `: { ok: boolean } {` where body opens on next token/line
            rest_of_line = clean.rstrip()
            if rest_of_line.endswith("=>"):
                body_opened = False
                continue
            return idx + 1

        if not body_opened and paren_depth == 0 and clean.rstrip().endswith(";") and idx >= start_line - 1:
            return idx + 1

    return min(len(lines), start_line + 45)


def _parse_js_ts(file_path: Path, raw_text: str, lines: List[str]) -> List[CodeBlock]:
    """Parse ESModule, TypeScript, CommonJS (.cjs), Class Methods, and Electron IPC/Preload symbols."""
    blocks: List[CodeBlock] = []
    seen_starts = set()
    total_lines = len(lines)

    decl_pattern = re.compile(
        r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?(function|class|interface|type)\s+([a-zA-Z0-9_$]+)"
        r"|^(?:export\s+)?(const|let)\s+([a-zA-Z0-9_$]+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[a-zA-Z0-9_$]+)\s*=>"
        r"|^\s*(?:module\.)?exports\.([a-zA-Z0-9_$]+)\s*=\s*(?:async\s*)?(?:function|\([^)]*\)\s*=>)"
        r"|^  (?:async\s+)?([a-zA-Z][a-zA-Z0-9_$]+)\s*\([^;]*\)\s*\{",
        re.MULTILINE,
    )

    for match in decl_pattern.finditer(raw_text):
        start_line = raw_text[:match.start()].count("\n") + 1
        if start_line in seen_starts:
            continue
        kw = match.group(1) or match.group(3) or ("method" if match.group(6) else "function")
        name = match.group(2) or match.group(4) or match.group(5) or match.group(6)
        if not name or name in {"if", "for", "while", "switch", "catch", "return", "constructor"}:
            continue
        seen_starts.add(start_line)
        end_line = _find_brace_end_line(lines, start_line)
        span = end_line - start_line + 1

        # Skip tiny 1-5 line boilerplate helpers in medium/large files so they don't pollute Quick Index
        if total_lines > 60 and (
            (name in TRIVIAL_HELPER_NAMES and span <= 18)
            or (span <= 3 and kw in {"function", "const", "let", "type"})
        ):
            continue

        # For class declarations that contain parsed methods, skip the outer class wrapper if huge
        if kw == "class" and total_lines > 120:
            continue

        full_block_text = "".join(lines[start_line - 1 : end_line])
        content_preview = "".join(lines[start_line - 1 : min(end_line, start_line + 75)])
        kind = "class" if kw == "class" else ("type" if kw in {"interface", "type"} else ("method" if kw == "method" else "function"))
        blocks.append(
            CodeBlock(
                file_path=file_path,
                name=name,
                kind=kind,
                start_line=start_line,
                end_line=end_line,
                signature=f"{kw} {name}",
                content=content_preview,
                summary=_build_rich_summary(lines, start_line, full_block_text),
            )
        )

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
        end_line = _find_brace_end_line(lines, start_line, max_span=90)
        full_block_text = "".join(lines[start_line - 1 : end_line])
        content_preview = "".join(lines[start_line - 1 : min(end_line, start_line + 60)])
        sym_name = f"{api_call}('{channel}')"
        blocks.append(
            CodeBlock(
                file_path=file_path,
                name=sym_name,
                kind="ipc",
                start_line=start_line,
                end_line=end_line,
                signature=sym_name,
                content=content_preview,
                summary=_build_rich_summary(lines, start_line, full_block_text),
            )
        )

    blocks.sort(key=lambda b: b.start_line)
    if not blocks:
        return _parse_generic(file_path, raw_text, lines)
    return blocks


def _parse_markdown(file_path: Path, lines: List[str]) -> List[CodeBlock]:
    """Parse Markdown manuals/docs by heading sections (#, ##, ###)."""
    blocks: List[CodeBlock] = []
    heading_indices = []
    for idx, line in enumerate(lines):
        m = re.match(r"^(#{1,3})\s+(.+)", line.strip())
        if m:
            heading_indices.append((idx + 1, m.group(2).strip()))

    if not heading_indices:
        return _parse_generic(file_path, "".join(lines), lines)

    SKIP_HEADINGS = {"문서 상태", "범위", "완료 기준", "권한·감사·안전", "책임"}
    for i, (start_line, title) in enumerate(heading_indices):
        if title.strip() in SKIP_HEADINGS:
            continue
        next_start = heading_indices[i + 1][0] - 1 if i + 1 < len(heading_indices) else len(lines)
        end_line = max(start_line, next_start)
        content = "".join(lines[start_line - 1 : min(end_line, start_line + 65)])
        blocks.append(
            CodeBlock(
                file_path=file_path,
                name=f"doc:{title[:45]}",
                kind="doc_section",
                start_line=start_line,
                end_line=end_line,
                signature=f"Section: {title[:60]}",
                content=content,
                summary=content.strip()[:220].replace("\n", " "),
            )
        )
    return blocks


def _parse_generic(file_path: Path, raw_text: str, lines: List[str], chunk_size: int = 50) -> List[CodeBlock]:
    blocks: List[CodeBlock] = []
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
                summary=content.strip()[:160].replace("\n", " "),
            )
        )
    return blocks
