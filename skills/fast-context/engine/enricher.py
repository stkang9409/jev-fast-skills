"""Domain-agnostic structural graph enricher: Sub-100ms Single-Pass Inverted Call Index, Caller-Priority Symbol Retention, Keyword-Filtered Entry Chains & DB Callees."""

import re
import subprocess
from pathlib import Path
from typing import List, Dict, Tuple, Set, Optional

from .parser import CodeBlock, parse_file, _find_brace_end_line, SQL_EXTRACT_RE, _STRING_COMMENT_RE
from .scanner import (
    get_repo_files,
    IGNORE_DIRS,
    TEST_PATTERNS,
    extract_intent_keywords,
    extract_domain_ascii_keywords,
    _kw_in_text,
)

# General camelCase/identifier discriminator property pattern (matches *Id, *Kind, *Version, *Mode, *Scope, *Purpose, *Format, *Profile, etc.)
DISC_RE = re.compile(
    r"\b([a-z][a-zA-Z0-9]*(?:Id|Kind|Version|Mode|Scope|Purpose|Format|Profile)|mode|kind|purpose|version|scope|format)\s*:\s*(['\"][a-zA-Z0-9_.-]{3,60}['\"])"
)
ERR_RE = re.compile(
    r"(?:fail\s*\(\s*|code\s*:\s*|new\s+[A-Za-z0-9_]*Error\s*\(\s*)(['\"][A-Z][A-Z0-9_]{5,}['\"])"
)
CONST_RE = re.compile(
    r"^(?:export\s+)?const\s+([A-Z][A-Z0-9_]{3,})\s*=\s*(?:['\"][^'\"]+['\"]|Object\.freeze)",
    re.MULTILINE,
)
REPO_CALL_RE = re.compile(
    r"\b(?:[a-zA-Z0-9_$]*[Rr]epository|repository|db|store|repo)\.([a-zA-Z0-9_$]{4,})\s*\("
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
CALL_TOKEN_RE = re.compile(r"(?<!\.)\b([a-zA-Z_$][a-zA-Z0-9_$]{4,})\s*\(")


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


def _load_narrow_code_cache(
    repo_root: Path,
    dir_groups: Optional[List[Tuple[str, Path]]],
    selected_files: Set[str],
    domain_kws: List[str],
) -> Tuple[Dict[str, str], Dict[str, int]]:
    """
    Load ONLY files relevant to the target directories, selected files, preload bridges,
    and files whose path matches the domain keywords (~25-45 files in <20ms instead of whole repo).
    """
    cache: Dict[str, str] = {}
    dir_file_counts: Dict[str, int] = {}
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

    group_prefixes: List[Tuple[str, str]] = []
    if dir_groups:
        for label, g_path in dir_groups:
            if label == "docs" or not g_path.is_dir():
                continue
            try:
                pref = str(g_path.resolve().relative_to(repo_root.resolve())).replace(chr(92), "/")
                group_prefixes.append((label, pref))
                dir_file_counts[label] = 0
            except ValueError:
                continue

    valid_rels: List[str] = []
    for rel in rel_files:
        if not rel or rel.endswith(".d.ts"):
            continue
        norm_rel = rel.replace(chr(92), "/")
        p = Path(norm_rel)
        if p.suffix.lower() not in code_exts:
            continue
        if any(part in IGNORE_DIRS for part in p.parts):
            continue
        if TEST_PATTERNS.search(norm_rel):
            continue
        valid_rels.append(norm_rel)
        for label, pref in group_prefixes:
            if norm_rel == pref or norm_rel.startswith(pref + "/"):
                dir_file_counts[label] = dir_file_counts.get(label, 0) + 1

    focused_prefixes = {
        pref for label, pref in group_prefixes if dir_file_counts.get(label, 0) <= 50
    }

    for norm_rel in valid_rels:
        rel_low = norm_rel.lower()
        in_selected = norm_rel in selected_files
        in_focused = any(norm_rel == pref or norm_rel.startswith(pref + "/") for pref in focused_prefixes)
        is_preload = "preload" in rel_low
        matches_domain_path = any(_kw_in_text(kw, rel_low) for kw in domain_kws) if domain_kws else False

        if not (in_selected or in_focused or is_preload or matches_domain_path):
            continue

        full_p = repo_root / norm_rel
        try:
            if full_p.is_file() and 0 < full_p.stat().st_size <= 350_000:
                cache[norm_rel] = full_p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

    return cache, dir_file_counts


def enrich_and_refine_blocks(
    repo_root: Path,
    dir_groups: Optional[List[Tuple[str, Path]]],
    scored_blocks: List[Tuple[CodeBlock, float]],
    task_intent: str = "",
    path_hints: Optional[List[str]] = None,
    max_per_file: int = 5,
) -> Tuple[List[Tuple[CodeBlock, float]], Dict[str, any]]:
    """
    Single-pass sub-100ms structural graph enrichment & symbol refinement:
    1. Narrowed code cache + Single-pass inverted call index (<25ms)
    2. 1-Hop Intra-Target Callee Promotion
    3. Caller-Priority Symbol Refinement per File (retains exported & 1-hop-from-export functions first)
    4. Keyword-Filtered Entry Chains (IPC/API -> Handler -> Preload -> UI)
    5. Selected-Block & Matched-Handler DB / Repository Links
    """
    selected_files: Set[str] = set()
    for blk, _ in scored_blocks:
        try:
            rel = str(blk.file_path.resolve().relative_to(repo_root.resolve())).replace(chr(92), "/")
        except ValueError:
            continue
        if not rel.endswith(".md"):
            selected_files.add(rel)

    domain_kws = extract_domain_ascii_keywords(task_intent, path_hints=path_hints, selected_files=selected_files)
    intent_ascii_kws = extract_domain_ascii_keywords(task_intent)
    all_kws = extract_intent_keywords(task_intent)

    code_cache, dir_file_counts = _load_narrow_code_cache(repo_root, dir_groups, selected_files, domain_kws)

    broad_prefixes: Set[str] = set()
    focused_prefixes: Set[str] = set()
    if dir_groups:
        for label, g_path in dir_groups:
            if label == "docs" or not g_path.is_dir():
                continue
            try:
                pref = str(g_path.resolve().relative_to(repo_root.resolve())).replace(chr(92), "/")
                if dir_file_counts.get(label, 0) > 50:
                    broad_prefixes.add(pref)
                else:
                    focused_prefixes.add(pref)
            except ValueError:
                continue

    # Build cross-file call check among focused selected files (avoiding local function name collisions)
    all_selected_List = [
        sf for sf in selected_files
        if not sf.endswith(".md")
        and not (
            any(sf == bp or sf.startswith(bp + "/") for bp in broad_prefixes)
            and not any(sf == fp or sf.startswith(fp + "/") for fp in focused_prefixes)
        )
    ]

    def _is_called_by_other_selected(rel_path: str) -> bool:
        exp_names = [
            b.name for b in parse_file(repo_root / rel_path)
            if b.signature.startswith("export ") and b.kind in {"function", "method", "class"} and len(b.name) >= 6
        ]
        if not exp_names:
            return False
        for other in all_selected_List:
            if other == rel_path:
                continue
            o_txt = code_cache.get(other, "")
            if not o_txt:
                continue
            for en in exp_names:
                if en in o_txt and f"function {en}" not in o_txt and re.search(rf"\b{re.escape(en)}\s*\(", o_txt):
                    return True
        return False

    # Count how many files per focused directory match intent_ascii_kws in their filename
    foc_dir_intent_matches: Dict[str, int] = {}
    if intent_ascii_kws:
        for fp in focused_prefixes:
            foc_dir_intent_matches[fp] = sum(
                1 for f_rel in code_cache
                if (f_rel == fp or f_rel.startswith(fp + "/"))
                and any(_kw_in_text(ik, Path(f_rel).stem.lower()) for ik in intent_ascii_kws)
            )

    cleaned_blocks: List[Tuple[CodeBlock, float]] = []
    for blk, sc in scored_blocks:
        try:
            rel = str(blk.file_path.resolve().relative_to(repo_root.resolve())).replace(chr(92), "/")
        except ValueError:
            rel = ""
        if rel and not rel.endswith(".md"):
            rel_low = rel.lower()
            in_focused = any(rel == fp or rel.startswith(fp + "/") for fp in focused_prefixes)
            in_broad = any(rel == bp or rel.startswith(bp + "/") for bp in broad_prefixes)
            if in_broad and not in_focused:
                if "repositor" in rel_low or (domain_kws and not any(_kw_in_text(kw, rel_low) for kw in domain_kws)):
                    selected_files.discard(rel)
                    continue
            elif in_focused and intent_ascii_kws:
                matching_fp = next((fp for fp in focused_prefixes if rel == fp or rel.startswith(fp + "/")), None)
                if matching_fp and foc_dir_intent_matches.get(matching_fp, 0) >= 3:
                    has_stem_match = any(_kw_in_text(ik, Path(rel).stem.lower()) for ik in intent_ascii_kws)
                    if not has_stem_match and not _is_called_by_other_selected(rel):
                        selected_files.discard(rel)
                        continue
        cleaned_blocks.append((blk, sc))
    scored_blocks = cleaned_blocks

    # Step A: 1-Hop Intra-Target Callee Promotion
    selected_block_texts = [
        code_cache.get(
            str(blk.file_path.resolve().relative_to(repo_root.resolve())).replace(chr(92), "/"),
            blk.content,
        )
        for blk, _ in scored_blocks
        if not str(blk.file_path).endswith(".md")
    ]
    combined_selected_text = "\n".join(selected_block_texts)

    if dir_groups:
        for label, g_path in dir_groups:
            if label == "docs" or not g_path.is_dir() or dir_file_counts.get(label, 0) > 50:
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
                if intent_ascii_kws and foc_dir_intent_matches.get(pref, 0) >= 3:
                    if not any(_kw_in_text(ik, Path(f_rel).stem.lower()) for ik in intent_ascii_kws) and not _is_called_by_other_selected(f_rel):
                        continue
                line_cnt = len(txt.splitlines())
                if line_cnt < 60:
                    continue
                blocks = parse_file(repo_root / f_rel)
                exp_funcs = [
                    b for b in blocks
                    if b.signature.startswith("export ") and b.kind in {"function", "method", "class"} and len(b.name) >= 6
                ]
                called = [
                    b for b in exp_funcs
                    if b.name in combined_selected_text
                    and f"function {b.name}" not in combined_selected_text
                    and re.search(rf"\b{re.escape(b.name)}\s*\(", combined_selected_text)
                ]
                if called:
                    kw_overlap = sum(1 for kw in all_kws if _kw_in_text(kw, f_rel.lower()) or _kw_in_text(kw, txt.lower()))
                    candidates_in_dir.append((kw_overlap, line_cnt, f_rel, called[:2]))
            candidates_in_dir.sort(key=lambda x: (x[0], x[1]), reverse=True)
            for _, _, f_rel, c_blocks in candidates_in_dir[:2]:
                for b in c_blocks:
                    scored_blocks.append((b, 0.62))
                selected_files.add(f_rel)

    # Single-Pass Inverted Call Index over code_cache (<20ms)
    call_index: Dict[str, List[Tuple[str, int]]] = {}
    def_prefixes = ("function ", "async function ", "export ", "def ", "class ", "//", "*", "module.exports")
    for f_rel, txt in code_cache.items():
        in_foc = any(f_rel == fp or f_rel.startswith(fp + "/") for fp in focused_prefixes)
        if in_foc and f_rel not in selected_files:
            continue
        for li, line in enumerate(txt.splitlines(), 1):
            s_line = line.strip()
            if not s_line or s_line.startswith(def_prefixes):
                continue
            for m in CALL_TOKEN_RE.finditer(s_line):
                fn = m.group(1)
                if fn not in {"return", "switch", "catch", "while", "typeof", "require", "import", "Object", "Array", "Number", "String", "Promise", "Buffer"}:
                    call_index.setdefault(fn, []).append((f_rel, li))

    # Step B: Compute Callers & Refine Per-File Symbols (Caller-Priority Retention up to max_per_file=5)
    symbol_callers: Dict[Tuple[str, str], List[str]] = {}
    blocks_by_rel: Dict[str, List[Tuple[CodeBlock, float]]] = {}
    doc_blocks: List[Tuple[CodeBlock, float]] = []

    for blk, sc in scored_blocks:
        try:
            rel = str(blk.file_path.resolve().relative_to(repo_root.resolve())).replace(chr(92), "/")
        except ValueError:
            rel = str(blk.file_path)
        if rel.endswith(".md"):
            doc_blocks.append((blk, sc))
        else:
            blocks_by_rel.setdefault(rel, []).append((blk, sc))

    refined_code_blocks: List[Tuple[CodeBlock, float]] = []
    all_selected_sym_names: Set[str] = set()
    exported_syms_in_selected: Dict[str, str] = {}

    for rel, current_list in blocks_by_rel.items():
        full_p = repo_root / rel
        all_file_blocks = parse_file(full_p)
        score_map = {b.name: sc for b, sc in current_list}

        exported_ranges: List[Tuple[int, int]] = []
        for b in all_file_blocks:
            if b.signature.startswith("export ") and len(b.name) >= 5:
                exported_syms_in_selected[b.name] = rel
                exported_ranges.append((b.start_line, b.end_line))

        candidate_pool: Dict[str, Tuple[CodeBlock, float]] = {b.name: (b, sc) for b, sc in current_list}
        for b in all_file_blocks:
            span = b.end_line - b.start_line + 1
            if b.kind in {"function", "method", "class"} and span >= 8 and b.name not in candidate_pool:
                candidate_pool[b.name] = (b, score_map.get(b.name, 0.58))

        ranked_for_file = []
        for b_name, (b, sc) in candidate_pool.items():
            is_exp = b.signature.startswith("export ") and b.kind in {"function", "method", "class"}
            ext_c: List[str] = []
            int_c: List[str] = []
            int_c_lines: List[int] = []

            if b.kind in {"function", "method", "class"} and len(b.name) >= 5:
                for caller_rel, c_line in call_index.get(b.name, []):
                    if caller_rel == rel:
                        if not (b.start_line <= c_line <= b.end_line):
                            l_tag = f"L{c_line}"
                            if l_tag not in int_c:
                                int_c.append(l_tag)
                                int_c_lines.append(c_line)
                    elif is_exp:
                        short_f = Path(caller_rel).name if Path(caller_rel).parent == Path(rel).parent else caller_rel
                        hit = f"{short_f}:L{c_line}"
                        if hit not in ext_c:
                            ext_c.append(hit)

            chosen_c = ext_c[:2] if ext_c else int_c[:1]
            if chosen_c:
                symbol_callers[(rel, b.name)] = chosen_c

            span = b.end_line - b.start_line + 1
            has_ext = len(ext_c) > 0
            has_int = len(int_c) > 0
            called_by_export = any(
                es <= cl <= ee for cl in int_c_lines for es, ee in exported_ranges
            )
            b_text_low = (b.name + " " + b.content).lower()
            kw_hit = sum(1 for kw in all_kws if len(kw) >= 2 and kw in b_text_low)
            # In-file fan-in bonus: functions called by many internal sites (e.g. shared error/validation builders) get higher weight
            fan_in_bonus = min(len(int_c), 5)

            # Structural Call-Graph Tiering:
            # Tier 4: Substantive (span >= 8) exported functions with external callers
            # Tier 3: Substantive (span >= 8) exported functions OR direct 1-hop callees of the file's exported functions
            # Tier 2: Substantive (span >= 8) internal functions with internal callers
            # Tier 1: Smaller functions with callers/exports
            tier = 0
            if span >= 8 and has_ext:
                tier = 4
            elif span >= 8 and (is_exp or called_by_export):
                tier = 3
            elif span >= 8 and has_int:
                tier = 2
            elif has_ext or has_int or is_exp:
                tier = 1

            if tier >= 2 or b_name in score_map:
                ranked_for_file.append((tier, round(sc, 2), kw_hit + fan_in_bonus, min(span, 200), b, max(sc, 0.60)))

        ranked_for_file.sort(key=lambda x: (x[0], x[1], x[2], x[3]), reverse=True)
        chosen_for_file = [(item[4], item[5]) for item in ranked_for_file[:max_per_file]]
        for b, _ in chosen_for_file:
            if b.kind in {"function", "method", "class"} and len(b.name) >= 5:
                all_selected_sym_names.add(b.name)
        refined_code_blocks.extend(chosen_for_file)

    final_blocks = refined_code_blocks + doc_blocks

    # Step C: File-Level Literals, Constants & Error Codes + Selected Block Repository Calls
    file_literals: Dict[str, str] = {}
    repo_method_calls: Dict[str, Set[str]] = {}
    core_repo_methods: Set[str] = set()

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

        parts = []
        if discs:
            parts.append("Literals: " + ", ".join(discs[:4]))
        elif consts:
            parts.append("Consts: " + ", ".join(consts[:3]))
        if errs:
            parts.append("Errors: " + ", ".join(errs[:4]))
        if parts:
            file_literals[rel] = "[" + " | ".join(parts) + "]"

    ignored_repo_methods = {"connect", "query", "release", "transaction", "runInTransaction"}
    for b, _ in refined_code_blocks:
        try:
            rel = str(b.file_path.resolve().relative_to(repo_root.resolve())).replace(chr(92), "/")
        except ValueError:
            continue
        txt = code_cache.get(rel, "")
        if not txt:
            continue
        full_body = "\n".join(txt.splitlines()[max(0, b.start_line - 1) : b.end_line])
        for m in REPO_CALL_RE.finditer(full_body):
            m_name = m.group(1)
            if m_name not in ignored_repo_methods:
                repo_method_calls.setdefault(m_name, set()).add(Path(rel).name)
                core_repo_methods.add(m_name)

    # Step D: Keyword-Filtered Entry Chains (IPC / API -> Handler -> Preload Bridge -> UI)
    entry_chains: List[Tuple[int, str]] = []
    preload_map: Dict[str, Tuple[str, int, str]] = {}
    for f_rel, txt in code_cache.items():
        if "preload" in f_rel.lower() or "ipcRenderer" in txt:
            for m in PRELOAD_BRIDGE_RE.finditer(txt):
                l_no = txt[:m.start()].count("\n") + 1
                preload_map[m.group(2)] = (f_rel, l_no, m.group(1))

    primary_call_tokens = sorted(
        {k for k in exported_syms_in_selected.keys() if len(k) >= 6},
        key=len,
        reverse=True,
    )
    seen_channels: Set[str] = set()
    covered_in_chains_or_db: Set[str] = set()

    for f_rel, txt in code_cache.items():
        if "handleIpc" not in txt and "ipcMain" not in txt:
            continue
        in_foc = any(f_rel == fp or f_rel.startswith(fp + "/") for fp in focused_prefixes)
        if in_foc and f_rel not in selected_files:
            continue
        lines = txt.splitlines(keepends=True)
        local_helper_to_primary: Dict[str, str] = {}
        for hm in re.finditer(r"^\s{0,6}(?:async\s+)?function\s+([a-zA-Z0-9_$]+)\s*\(", txt, re.MULTILINE):
            h_name = hm.group(1)
            hs_line = txt[:hm.start()].count("\n") + 1
            he_line = _find_brace_end_line(lines, hs_line, max_span=150)
            h_body = "".join(lines[hs_line - 1 : he_line])
            for sym in primary_call_tokens:
                if sym != h_name and sym in h_body and re.search(rf"\b{re.escape(sym)}\s*\(", h_body):
                    local_helper_to_primary[h_name] = sym
                    break

        for m in IPC_HANDLER_RE.finditer(txt):
            ch = m.group(1)
            if ch in seen_channels:
                continue
            ch_low = ch.lower()
            ch_matches_kw = any(_kw_in_text(kw, ch_low) for kw in domain_kws) if domain_kws else True

            s_line = txt[:m.start()].count("\n") + 1
            e_line = _find_call_end_line(lines, s_line, max_span=65)
            body = "".join(lines[s_line - 1 : e_line])
            prim_called = [sym for sym in primary_call_tokens if sym in body and re.search(rf"\b{re.escape(sym)}\s*\(", body)]
            if not prim_called and local_helper_to_primary:
                for h_name, target_sym in local_helper_to_primary.items():
                    if h_name in body and re.search(rf"\b{re.escape(h_name)}\s*\(", body):
                        prim_called.append(f"{h_name} → {target_sym}")

            repo_in_handler: List[str] = []
            if ch_matches_kw:
                for rm in REPO_CALL_RE.finditer(body):
                    rm_name = rm.group(1)
                    if rm_name not in ignored_repo_methods:
                        repo_in_handler.append(rm_name)
                        if prim_called or any(kw in rm_name.lower() for kw in domain_kws):
                            repo_method_calls.setdefault(rm_name, set()).add(f"{Path(f_rel).name}:L{s_line}")

            if domain_kws and not ch_matches_kw and not prim_called:
                continue
            if not prim_called and not repo_in_handler:
                continue

            seen_channels.add(ch)
            covered_in_chains_or_db.add(f_rel)
            called_label = prim_called[0] if prim_called else repo_in_handler[0]
            pre = preload_map.get(ch)
            priority = (4 if (ch_matches_kw and prim_called) else (2 if ch_matches_kw else 1)) + (1 if pre else 0)
            chain_parts = [f"`'{ch}'` → handler: {f_rel}:L{s_line}-L{e_line} (`{called_label}`)"]
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

    # Step E: Resolve 1-Hop DB / Repository Callees (ONLY methods called by selected blocks or matched handlers)
    db_links_ranked: List[Tuple[int, str]] = []
    for m_name, callers in sorted(repo_method_calls.items()):
        pat = re.compile(rf"^\s*(?:async\s+)?(?:function\s+)?({re.escape(m_name)})\s*\(", re.MULTILINE)
        for f_rel, txt in code_cache.items():
            if m_name not in txt:
                continue
            if f_rel in selected_files and "repositor" not in f_rel.lower():
                continue
            m = pat.search(txt)
            if not m:
                continue
            covered_in_chains_or_db.add(f_rel)
            s_line = txt[:m.start()].count("\n") + 1
            lines = txt.splitlines(keepends=True)
            e_line = _find_brace_end_line(lines, s_line, max_span=150)
            body = "".join(lines[s_line - 1 : e_line])
            sqls = list(dict.fromkeys(sm.group(1) for sm in SQL_EXTRACT_RE.finditer(body)))
            sql_note = f" | SQL: {', '.join(sqls[:3])}" if sqls else ""
            caller_note = f" [← {', '.join(sorted(callers)[:2])}]"
            core_bonus = 5 if m_name in core_repo_methods else 0
            kw_score = core_bonus + sum(2 for kw in domain_kws if kw in m_name.lower()) + len(callers)
            db_links_ranked.append((kw_score, f"- {f_rel}: `{m_name}` L{s_line}-L{e_line}{caller_note}{sql_note}"))
            break

    db_links_ranked.sort(key=lambda x: x[0], reverse=True)

    # Step F: Module Map per Target Directory (broad dirs omitted if already covered by Entry Chains / DB Links)
    dir_module_maps: Dict[str, str] = {}
    if dir_groups:
        all_group_prefixes = []
        for lbl, gp in dir_groups:
            if lbl != "docs" and gp.is_dir():
                try:
                    all_group_prefixes.append((lbl, str(gp.resolve().relative_to(repo_root.resolve())).replace(chr(92), "/")))
                except ValueError:
                    pass

        for label, g_path in dir_groups:
            if label == "docs" or not g_path.is_dir():
                continue
            is_broad = dir_file_counts.get(label, 0) > 50
            if is_broad:
                continue
            try:
                g_rel_prefix = str(g_path.resolve().relative_to(repo_root.resolve())).replace(chr(92), "/")
            except ValueError:
                continue
            child_prefixes = [
                other_pref for _, other_pref in all_group_prefixes
                if other_pref != g_rel_prefix and other_pref.startswith(g_rel_prefix + "/")
            ]

            dir_files = []
            for f in sorted(code_cache.keys()):
                if f in selected_files or f in covered_in_chains_or_db:
                    continue
                if not (f == g_rel_prefix or f.startswith(g_rel_prefix + "/")):
                    continue
                if any(f == cp or f.startswith(cp + "/") for cp in child_prefixes):
                    continue
                dir_files.append(f)

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
                        if kv not in seen_md:
                            seen_md.add(kv)
                            mod_discs.append(f"{kv} L{li}")
                info_parts = exp_funcs[:2] + mod_discs[:2]
                if info_parts:
                    mod_entries.append(f"`{sub_rel}` ({', '.join(info_parts)})")
                else:
                    mod_entries.append(f"`{sub_rel}`")
            if mod_entries:
                dir_module_maps[label] = "- [Module Map (Other Files)]: " + ", ".join(mod_entries)

    return final_blocks, {
        "symbol_callers": symbol_callers,
        "file_literals": file_literals,
        "dir_module_maps": dir_module_maps,
        "entry_chains": [c for _, c in sorted(entry_chains, key=lambda x: x[0], reverse=True)[:10]],
        "db_links": [d for _, d in db_links_ranked[:8]],
    }
