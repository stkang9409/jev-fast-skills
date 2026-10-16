"""AST and signature parser for code slicing."""

import ast
import re
from pathlib import Path
from typing import List, Dict, Any, Optional


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
            "summary": self.summary,
        }


def parse_file(file_path: Path, max_lines_full: int = 50) -> List[CodeBlock]:
    """Parse a source file into semantic symbols and code blocks."""
    if not file_path.is_file():
        return []

    try:
        raw_text = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    lines = raw_text.splitlines(keepends=True)
    total_lines = len(lines)

    if total_lines == 0:
        return []

    # Small files: take the entire file as a single coherent block
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
                summary=raw_text.strip()[:150].replace("\n", " "),
            )
        ]

    suffix = file_path.suffix.lower()
    if suffix == ".py":
        return _parse_python(file_path, raw_text, lines)
    elif suffix in {".js", ".jsx", ".ts", ".tsx", ".mjs"}:
        return _parse_js_ts(file_path, raw_text, lines)
    else:
        return _parse_generic(file_path, raw_text, lines)


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
            
            # Add methods
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
            
            # If class has no methods or is short, keep class itself
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


def _parse_js_ts(file_path: Path, raw_text: str, lines: List[str]) -> List[CodeBlock]:
    blocks = []
    func_pattern = re.compile(
        r"^(?:export\s+)?(?:async\s+)?function\s+([a-zA-Z0-9_$]+)\s*\(|^const\s+([a-zA-Z0-9_$]+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>|^class\s+([a-zA-Z0-9_$]+)",
        re.MULTILINE,
    )

    for match in func_pattern.finditer(raw_text):
        name = match.group(1) or match.group(2) or match.group(3)
        start_char = match.start()
        start_line = raw_text[:start_char].count("\n") + 1
        
        # Estimate end line (look for matching brackets or up to 60 lines)
        end_line = min(len(lines), start_line + 40)
        content = "".join(lines[start_line - 1 : end_line])
        blocks.append(
            CodeBlock(
                file_path=file_path,
                name=name,
                kind="function",
                start_line=start_line,
                end_line=end_line,
                signature=f"function {name}",
                content=content,
                summary=content.strip()[:120].replace("\n", " "),
            )
        )

    if not blocks:
        return _parse_generic(file_path, raw_text, lines)
    return blocks


def _parse_generic(file_path: Path, raw_text: str, lines: List[str], chunk_size: int = 50) -> List[CodeBlock]:
    """Chunk generic files into manageable 50-line sections."""
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
