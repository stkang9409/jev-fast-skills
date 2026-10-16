"""Domain-agnostic structural graph enricher: 1-Hop Call-Graph Neighbors, Callers, Entry Chains (IPC/API->Handler->Bridge->UI), Module Map, Literals & DB Callees."""

import re
import subprocess
from pathlib import Path
from typing import List, Dict, Tuple, Set, Optional

from .parser import CodeBlock, parse_file, _find_brace_end_line, SQL_EXTRACT_RE, _STRING_COMMENT_RE
from .scanner import get_repo_files, IGNORE_DIRS, TEST_PATTERNS

DISC_RE = re.compile(
    r"\b(purpose|sourceKind|formProfileId|profileId|contractVersion|mode)\s*:\s*(['\"][a-zA-Z0-9_.-]+['\"])"
)
ERR_RE = re.compile(
    r"(?:fail\s*\(\s*|code\s*:\s*|new\s+[A-Za-z0-9_]*Error\s*\(\s*)(['\"][A-Z][A-Z0-9_]{5,}['\"])"
)
CONST_RE = re.compile(
    r"^(?:export\s+)?const\s+([A-Z][A-Z0-9_]{3,})\s*=\s*(?:['\"][^'\"]+['\"]|Object\.freeze)",
    re.MULTILINE,
)
REPO_CALL_RE = re.compile(
    r"\b(?:repository|db|store|repo)\.([a-zA-Z0-9_$]{4,})\s*\("
)
IPC_HANDLER_RE = re.compile(
    r"(?:handleIpc|ipcMain\.(?:handle|on))\s*\(\s*['\"]([^'\"]+)['\"]"
)
PRELOAD_BRIDGE_RE = re.compile(
    r"([a-zA-Z0-9_$]+)\s*:\s*\([^)]*\)\s*=>\s*ipcRenderer\.(?:invoke|send)\(\s*['\"]([^'\"]+)['\"]"
)
FETCH_API_RE = re.compile(
    r"fetch\s*\(\s*['\"`](/api/[a-zA-Z0-9_/-]+)"
)


def _find_call_end_line(lines: List[str], start_line: int, max_span: int = 80) -> int:
    """Find the exact closing line of a call expression like `handleIpc('...', async (...) => { ... })`."""
    paren_depth = 0
    seen_open = False
    limit = min(len(lines), start_line - 1 + max_span)
    for idx in range(start_line - 1, limit):
        clean = _STRING_COMMENT_RE.sub("", lines[idx])
        for ch in clean:
            if ch == "(":
                paren_depth += 1
                seen_open = True
            elif ch == ")":
                paren_depth = max(0, paren_depth - 1)
                if seen_open and paren_depth == 0:
                    return idx + 1
    return min(len(lines), start_line + 25)


def _load_repo_code_cache(repo_root: Path) -> Dict[str, str]:
    """Load non-test source files in the repository in ~120ms via git ls-files."""
    cache: Dict[str, str] = {}
    code_exts = {".ts", ".tsx", ".js", ".jsx", ".cjs", ".mjs", ".py"}
    try:
        rel_files = subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=str(repo_root),
            text=True,
            timeout=3,
        ).splitlines()
    except Exception:
        rel_files = [
            str(p.relative_to(repo_root))
            for p in get_repo_files(repo_root, include_tests=False, include_docs=False)
        ]

    for rel in rel_files:
        if not rel or rel.endswith(".d.ts"):
            continue
        p = Path(rel)
        if p.suffix.lower() not in code_exts:
            continue
        if any(part in IGNORE_DIRS for part in p.parts):
            continue
        if TEST_PATTERNS.search(rel.replace(chr(92), "/")):
            continue
        full_p = repo_root / p
        try:
            if full_p.is_file() and 0 < full_p.stat().st_size <= 350_000:
                cache[rel.replace(chr(92), "/")] = full_p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
    return cache


def promote_call_graph_neighbors(
    repo_root: Path,
    dir_groups: Optional[List[Tuple[str, Path]]],
    scored_blocks: List[Tuple[CodeBlock, float]],
) -> List[Tuple[CodeBlock, float]]:
    """
    1-Hop Intra-Target Callee Promotion:
    If a substantive module (>=60 lines) inside a target directory defines an exported function
    that is directly called inside one of the Stage-2 selected blocks (`blk.content`),
    promote that exported function (up to 2 files per target directory).
    """
    if not dir_groups:
        return scored_blocks

    code_cache = _load_repo_code_cache(repo_root)
    selected_files: Set[str] = set()
    selected_block_contents: List[str] = []
    for blk, _ in scored_blocks:
        try:
            rel = str(blk.file_path.resolve().relative_to(repo_root.resolve())).replace(chr(92), "/")
        except ValueError:
            continue
        if not rel.endswith(".md"):
            selected_files.add(rel)
            selected_block_contents.append(blk.content)

    combined_selected_content = "\n".join(selected_block_contents)
    promoted: List[Tuple[CodeBlock, float]] = list(scored_blocks)

    for label, g_path in dir_groups:
        if label == "docs" or not g_path.is_dir():
            continue
        try:
            pref = str(g_path.resolve().relative_to(repo_root.resolve())).replace(chr(92), "/")
        except ValueError:
            continue

        candidates_in_dir = []
        for f_rel, txt in code_cache.items():
            if f_rel in selected_files:
                continue
            if not (f_rel == pref or f_rel.startswith(pref + "/")):
                continue
            line_cnt = len(txt.splitlines())
            if line_cnt < 60:
                continue
            blocks = parse_file(repo_root / f_rel)
            exp_funcs = [
                b for b in blocks
                if b.signature.startswith("export ") and b.kind in {"function", "method", "class"} and len(b.name) >= 6
            ]
            called = [b for b in exp_funcs if re.search(rf"\b{re.escape(b.name)}\s*\(", combined_selected_content)]
            if called:
                candidates_in_dir.append((line_cnt, f_rel, called[:2]))

        candidates_in_dir.sort(key=lambda x: x[0], reverse=True)
        for _, f_rel, c_blocks in candidates_in_dir[:2]:
            for b in c_blocks:
                promoted.append((b, 0.61))
            selected_files.add(f_rel)

    return promoted


def build_enriched_metadata(
    repo_root: Path,
    dir_groups: Optional[List[Tuple[str, Path]]],
    scored_blocks: List[Tuple[CodeBlock, float]],
) -> Dict[str, any]:
    """
    Compute 5 zero-overfitting structural enrichments in <200ms:
    1. symbol_callers: Dict[(rel_file, sym_name), List[str]] -> e.g. ["prepare.ts:L266"]
    2. file_literals: Dict[rel_file, str] -> e.g. "[Literals: purpose=\"operation\" L70 | Errors: CLAIM_... L422]"
    3. dir_module_maps: Dict[dir_label, str] -> e.g. "- [Module Map (Other Files)]: ..."
    4. entry_chains: List[str] -> IPC/API -> Handler -> Preload/Bridge -> UI call site
    5. db_links: List[str] -> 1-hop repository/DB method definitions & SQL tables
    """
    code_cache = _load_repo_code_cache(repo_root)

    selected_files: Set[str] = set()
    all_selected_sym_names: Set[str] = set()

    for blk, _ in scored_blocks:
        try:
            rel = str(blk.file_path.resolve().relative_to(repo_root.resolve())).replace(chr(92), "/")
        except ValueError:
            rel = str(blk.file_path)
        if not rel.endswith(".md"):
            selected_files.add(rel)
            if blk.kind in {"function", "method", "class"} and len(blk.name) >= 5:
                all_selected_sym_names.add(blk.name)

    exported_syms_in_selected: Dict[str, str] = {}
    for rel in selected_files:
        full_p = repo_root / rel
        for b in parse_file(full_p):
            if b.signature.startswith("export ") and len(b.name) >= 5:
                exported_syms_in_selected[b.name] = rel

    # 1. Symbol Callers ([← file:Lline])
    symbol_callers: Dict[Tuple[str, str], List[str]] = {}
    for blk, _ in scored_blocks:
        if blk.kind not in {"function", "method", "class"} or len(blk.name) < 5:
            continue
        try:
            def_rel = str(blk.file_path.resolve().relative_to(repo_root.resolve())).replace(chr(92), "/")
        except ValueError:
            def_rel = str(blk.file_path)
        sym = blk.name
        call_pat = re.compile(rf"\b{re.escape(sym)}\s*\(")
        ext_callers: List[str] = []
        int_callers: List[str] = []

        for f_rel, txt in code_cache.items():
            if sym not in txt:
                continue
            for m in call_pat.finditer(txt):
                line_no = txt[:m.start()].count("\n") + 1
                line_str = txt.splitlines()[line_no - 1].strip() if line_no <= len(txt.splitlines()) else ""
                if line_str.startswith(("function ", "async function ", "export ", "def ", "class ", "//", "*")):
                    continue
                if f_rel == def_rel:
                    if not (blk.start_line <= line_no <= blk.end_line):
                        int_callers.append(f"L{line_no}")
                else:
                    short_f = Path(f_rel).name if Path(f_rel).parent == Path(def_rel).parent else f_rel
                    hit = f"{short_f}:L{line_no}"
                    if hit not in ext_callers:
                        ext_callers.append(hit)
                        if len(ext_callers) >= 2:
                            break
            if len(ext_callers) >= 2:
                break

        chosen = ext_callers[:2] if ext_callers else int_callers[:1]
        if chosen:
            symbol_callers[(def_rel, sym)] = chosen

    # 2. File-Level Literals, Constants & Error Codes
    file_literals: Dict[str, str] = {}
    repo_method_calls: Dict[str, Set[str]] = {}

    for rel in selected_files:
        txt = code_cache.get(rel, "")
        if not txt:
            continue
        lines = txt.splitlines()
        discs: List[str] = []
        seen_d: Set[str] = set()
        for i, l in enumerate(lines, 1):
            for m in DISC_RE.finditer(l):
                kv = f"{m.group(1)}={m.group(2)}"
                if kv not in seen_d:
                    seen_d.add(kv)
                    discs.append(f"{kv} L{i}")

        consts: List[str] = []
        for m in CONST_RE.finditer(txt):
            l_no = txt[:m.start()].count("\n") + 1
            consts.append(f"{m.group(1)} L{l_no}")

        errs: List[str] = []
        seen_e: Set[str] = set()
        for i, l in enumerate(lines, 1):
            for m in ERR_RE.finditer(l):
                c_name = m.group(1).strip("'\"")
                if c_name not in seen_e:
                    seen_e.add(c_name)
                    errs.append(f"{c_name} L{i}")

        for m in REPO_CALL_RE.finditer(txt):
            repo_method_calls.setdefault(m.group(1), set()).add(Path(rel).name)

        parts = []
        if discs:
            parts.append("Literals: " + ", ".join(discs[:4]))
        elif consts:
            parts.append("Consts: " + ", ".join(consts[:3]))
        if errs:
            parts.append("Errors: " + ", ".join(errs[:4]))
        if parts:
            file_literals[rel] = "[" + " | ".join(parts) + "]"

    # 3. Module Map per Target Directory (for remaining unselected files)
    dir_module_maps: Dict[str, str] = {}
    if dir_groups:
        for label, g_path in dir_groups:
            if label == "docs" or not g_path.is_dir():
                continue
            try:
                g_rel_prefix = str(g_path.resolve().relative_to(repo_root.resolve())).replace(chr(92), "/")
            except ValueError:
                continue
            dir_files = sorted(
                f for f in code_cache
                if (f == g_rel_prefix or f.startswith(g_rel_prefix + "/")) and f not in selected_files
            )
            if not dir_files:
                continue
            mod_entries: List[str] = []
            for df in dir_files[:10]:
                sub_rel = df[len(g_rel_prefix) + 1 :] if df.startswith(g_rel_prefix + "/") else Path(df).name
                blocks = parse_file(repo_root / df)
                exp_funcs = [
                    f"{b.name} L{b.start_line}-L{b.end_line}"
                    for b in blocks
                    if b.signature.startswith("export ") and b.kind in {"function", "method", "class"}
                ]
                if not exp_funcs and blocks:
                    exp_funcs = [f"{blocks[-1].name} L{blocks[-1].start_line}-L{blocks[-1].end_line}"]
                mod_txt = code_cache.get(df, "")
                mod_discs: List[str] = []
                seen_md: Set[str] = set()
                for li, l_str in enumerate(mod_txt.splitlines(), 1):
                    for dm in DISC_RE.finditer(l_str):
                        kv = f"{dm.group(1)}={dm.group(2)}"
                        if kv not in seen_md and dm.group(1) in {"purpose", "sourceKind", "mode"}:
                            seen_md.add(kv)
                            mod_discs.append(f"{kv} L{li}")
                info_parts = exp_funcs[:2] + mod_discs[:2]
                if info_parts:
                    mod_entries.append(f"`{sub_rel}` ({', '.join(info_parts)})")
                else:
                    mod_entries.append(f"`{sub_rel}`")
            if mod_entries:
                dir_module_maps[label] = "- [Module Map (Other Files)]: " + ", ".join(mod_entries)

    # 4. 1-Hop DB / Repository Callees & SQL Table Siblings
    db_links: List[str] = []
    found_sql_tables: Set[str] = set()
    repo_files_found: Set[str] = set()
    resolved_methods: Set[str] = set()

    for m_name, callers in sorted(repo_method_calls.items()):
        pat = re.compile(rf"^\s*(?:async\s+)?(?:function\s+)?({re.escape(m_name)})\s*\(", re.MULTILINE)
        for f_rel, txt in code_cache.items():
            if f_rel in selected_files or m_name not in txt:
                continue
            m = pat.search(txt)
            if not m:
                continue
            s_line = txt[:m.start()].count("\n") + 1
            lines = txt.splitlines(keepends=True)
            e_line = _find_brace_end_line(lines, s_line, max_span=150)
            body = "".join(lines[s_line - 1 : e_line])
            sqls = list(dict.fromkeys(sm.group(1) for sm in SQL_EXTRACT_RE.finditer(body)))
            found_sql_tables.update(sqls)
            repo_files_found.add(f_rel)
            resolved_methods.add(m_name)
            sql_note = f" | SQL: {', '.join(sqls[:3])}" if sqls else ""
            caller_note = f" [← {', '.join(sorted(callers)[:2])}]"
            db_links.append(f"- {f_rel}: `{m_name}` L{s_line}-L{e_line}{caller_note}{sql_note}")
            break

    for rf in sorted(repo_files_found):
        txt = code_cache.get(rf, "")
        lines = txt.splitlines(keepends=True)
        for m in re.finditer(r"^\s{2,4}(?:async\s+)?([a-zA-Z][a-zA-Z0-9_$]{3,})\s*\([^;]*\)\s*\{", txt, re.MULTILINE):
            m_name = m.group(1)
            if m_name in resolved_methods or m_name in {"if", "for", "while", "switch", "catch"}:
                continue
            if not m_name.startswith(("list", "load", "get", "find")):
                continue
            s_line = txt[:m.start()].count("\n") + 1
            e_line = _find_brace_end_line(lines, s_line, max_span=150)
            body = "".join(lines[s_line - 1 : e_line])
            shared_tables = [t for t in found_sql_tables if t in body]
            if shared_tables:
                resolved_methods.add(m_name)
                db_links.append(f"- {rf}: `{m_name}` L{s_line}-L{e_line} | SQL: {', '.join(shared_tables[:2])}")

    # 5. Entry Chains: IPC (Channel -> Handler -> Preload Bridge -> UI) or Web API (/api/... -> Route -> UI)
    entry_chains: List[str] = []
    preload_map: Dict[str, Tuple[str, int, str]] = {}
    for f_rel, txt in code_cache.items():
        if "preload" in f_rel.lower() or "ipcRenderer" in txt:
            for m in PRELOAD_BRIDGE_RE.finditer(txt):
                l_no = txt[:m.start()].count("\n") + 1
                preload_map[m.group(2)] = (f_rel, l_no, m.group(1))

    primary_call_tokens = sorted(all_selected_sym_names | set(exported_syms_in_selected.keys()), key=len, reverse=True)
    secondary_call_tokens = sorted(resolved_methods, key=len, reverse=True)
    seen_channels: Set[str] = set()

    for f_rel, txt in code_cache.items():
        if "handleIpc" not in txt and "ipcMain" not in txt:
            continue
        is_selected = f_rel in selected_files
        is_dispatcher = any(k in Path(f_rel).name.lower() for k in ("ipc", "main", "router", "index", "server")) and (
            Path(f_rel).parent == repo_root or len(Path(f_rel).parts) <= 2
        )
        if not (is_selected or is_dispatcher):
            continue

        lines = txt.splitlines(keepends=True)
        local_helper_to_primary: Dict[str, str] = {}
        for hm in re.finditer(r"^\s{0,6}(?:async\s+)?function\s+([a-zA-Z0-9_$]+)\s*\(", txt, re.MULTILINE):
            h_name = hm.group(1)
            hs_line = txt[:hm.start()].count("\n") + 1
            he_line = _find_brace_end_line(lines, hs_line, max_span=150)
            h_body = "".join(lines[hs_line - 1 : he_line])
            for sym in primary_call_tokens:
                if sym != h_name and re.search(rf"\b{re.escape(sym)}\s*\(", h_body):
                    local_helper_to_primary[h_name] = sym
                    break

        for m in IPC_HANDLER_RE.finditer(txt):
            ch = m.group(1)
            if ch in seen_channels:
                continue
            s_line = txt[:m.start()].count("\n") + 1
            e_line = _find_call_end_line(lines, s_line, max_span=65)
            body = "".join(lines[s_line - 1 : e_line])
            prim_called = [sym for sym in primary_call_tokens if re.search(rf"\b{re.escape(sym)}\s*\(", body)]
            if not prim_called and local_helper_to_primary:
                for h_name, target_sym in local_helper_to_primary.items():
                    if re.search(rf"\b{re.escape(h_name)}\s*\(", body):
                        prim_called.append(f"{h_name} → {target_sym}")
            sec_called = [sym for sym in secondary_call_tokens if re.search(rf"\b{re.escape(sym)}\s*\(", body)] if not prim_called else []
            called = prim_called or sec_called
            if not called and not is_selected:
                continue
            seen_channels.add(ch)
            priority = 2 if prim_called else (1 if is_selected else 0)
            chain_parts = [f"`'{ch}'` → handler: {f_rel}:L{s_line}-L{e_line}"]
            if called:
                chain_parts[0] += f" (`{called[0]}`)"
            pre = preload_map.get(ch)
            if pre:
                pre_file, pre_line, bridge_fn = pre
                chain_parts.append(f"preload: {pre_file}:L{pre_line} (`{bridge_fn}`)")
                ui_sites: List[str] = []
                for u_rel, u_txt in code_cache.items():
                    if u_rel == pre_file or f".{bridge_fn}(" not in u_txt:
                        continue
                    idx = u_txt.find(f".{bridge_fn}(")
                    if idx != -1:
                        u_line = u_txt[:idx].count("\n") + 1
                        ui_sites.append(f"{u_rel}:L{u_line}")
                        if len(ui_sites) >= 2:
                            break
                if ui_sites:
                    chain_parts.append(f"ui: {', '.join(ui_sites)}")
            entry_chains.append((priority, "- " + " → ".join(chain_parts)))

    if not entry_chains:
        seen_routes: Set[str] = set()
        for rel in selected_files:
            txt = code_cache.get(rel, "")
            for m in FETCH_API_RE.finditer(txt):
                api_path = m.group(1).rstrip("/")
                if api_path in seen_routes:
                    continue
                seen_routes.add(api_path)
                call_line = txt[:m.start()].count("\n") + 1
                route_cand = f"app{api_path}/route.ts"
                if route_cand in code_cache:
                    r_lines = len(code_cache[route_cand].splitlines())
                    entry_chains.append((1, f"- `'{api_path}'` → caller: {rel}:L{call_line} → route: {route_cand}:L1-L{r_lines}"))

    return {
        "symbol_callers": symbol_callers,
        "file_literals": file_literals,
        "dir_module_maps": dir_module_maps,
        "entry_chains": [c for _, c in sorted(entry_chains, key=lambda x: x[0], reverse=True)[:10]],
        "db_links": db_links[:8],
    }
