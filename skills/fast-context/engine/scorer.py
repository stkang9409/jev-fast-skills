"""Batched parallel scorer using Pure TypeSafe Jev multilingual semantic evaluation."""

import os
import time
import httpx
import concurrent.futures
from pathlib import Path
from typing import List, Dict, Tuple
from dotenv import load_dotenv

from .parser import CodeBlock
from .scanner import extract_intent_keywords

load_dotenv(dotenv_path=Path(__file__).resolve().parents[3] / ".env")
load_dotenv(dotenv_path=Path.home() / ".gemini" / ".env")
load_dotenv()

JEV_URL = os.environ.get("TYPESAFE_API_URL", "https://api.typesafe.ai/v1/systemone")
CLIENT = httpx.Client(
    http2=True,
    timeout=15.0,
    limits=httpx.Limits(max_connections=24, max_keepalive_connections=12),
)


def _call_jev_single(body: dict, api_key: str) -> dict:
    for attempt in range(3):
        try:
            resp = CLIENT.post(
                JEV_URL,
                json=body,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.status_code in {429, 502, 503, 529} and attempt < 2:
                time.sleep(0.3 * (2 ** attempt))
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception:
            if attempt == 2:
                return {"answers": {}}
            time.sleep(0.2)
    return {"answers": {}}


def score_files(
    task_intent: str,
    file_summaries: Dict[str, str],
    threshold: float = 0.60,
    max_files: int = 24,
    max_workers: int = 8,
) -> List[Tuple[str, float]]:
    """Stage 1: Score candidate files in parallel via TypeSafe Jev without lexical distortion."""
    api_key = os.environ.get("TYPESAFE_API_KEY")
    if not api_key:
        raise ValueError("TYPESAFE_API_KEY is not set.")

    items = list(file_summaries.items())
    if not items:
        return []

    intent_lower = task_intent.lower()
    chunk_size = 24
    batches = [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]

    scored_files: List[Tuple[str, float]] = []

    def process_batch(batch):
        questions = {}
        idx_to_path = {}
        for idx, (path, preview) in enumerate(batch):
            q_id = f"q_{idx}"
            idx_to_path[q_id] = path
            safe_preview = preview.replace("\n", " ")[:240]
            questions[q_id] = {
                "type": "noul",
                "instructions": (
                    f"Is the file '{path}' directly relevant and needed for the task: '{task_intent}'? "
                    f"Summary: {safe_preview}"
                ),
            }

        body = {
            "model": os.environ.get("TYPESAFE_MODEL", "jev-latest"),
            "state": {
                "task": task_intent,
                "batch_file_count": len(batch),
            },
            "questions": questions,
        }

        res = _call_jev_single(body, api_key)
        answers = res.get("answers", {})
        results = []
        for q_id, path in idx_to_path.items():
            raw_jev = float(answers.get(q_id, {}).get("noul", 0.0))
            score = raw_jev
            fname = Path(path).name.lower()
            stem = Path(path).stem.lower()

            # Only boost if the user explicitly named the exact file in the query
            if (fname not in {"skill.md", "readme.md", "index.ts", "index.js", "index.cjs", "page.tsx"}) and (
                fname in intent_lower or (len(stem) >= 5 and stem in intent_lower)
            ):
                score = max(score, 0.92)
            results.append((path, score))
        return results

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(process_batch, b) for b in batches]
        for f in concurrent.futures.as_completed(futures):
            try:
                scored_files.extend(f.result())
            except Exception:
                pass

    scored_files.sort(key=lambda x: x[1], reverse=True)
    filtered = [item for item in scored_files if item[1] >= threshold]

    if not filtered and scored_files:
        filtered = scored_files[:3]

    doc_filtered = [item for item in filtered if item[0].lower().endswith(".md")][:4]
    code_filtered = [item for item in filtered if not item[0].lower().endswith(".md")][: max(1, max_files - len(doc_filtered))]
    combined = code_filtered + doc_filtered
    combined.sort(key=lambda x: x[1], reverse=True)
    return combined[:max_files]


def _structural_priority(blk: CodeBlock, keywords: List[str], caller_count: int = 0) -> float:
    """
    Domain-agnostic structural priority: prioritizes functions with callers, substantive
    exported functions, and blocks whose name/summary/body match task intent keywords.
    """
    span = max(1, blk.end_line - blk.start_line + 1)
    is_exported = blk.signature.startswith("export ")
    prio = 0.0
    if caller_count > 0 and span >= 8:
        prio += 3.0 + min(caller_count, 6) * 0.4
    elif caller_count > 0:
        prio += 1.5
    if is_exported and span >= 8:
        prio += 3.0
    elif is_exported:
        prio += 1.0
    if blk.kind in {"function", "method", "ipc", "doc_section"}:
        prio += 1.5
    elif blk.kind == "type":
        prio -= 1.5
    if span >= 12:
        prio += 1.5
    elif span <= 5:
        prio -= 2.5
    s_low = blk.summary.lower()
    n_low = blk.name.lower()
    c_low = blk.content.lower()
    for kw in keywords:
        if len(kw) >= 2 and kw in n_low:
            prio += 2.2
        elif len(kw) >= 2 and (kw in s_low or kw in c_low):
            prio += 1.2
    return prio


def score_symbols(
    task_intent: str,
    blocks: List[CodeBlock],
    threshold: float = 0.60,
    max_blocks: int = 75,
    max_per_file: int = 5,
    max_workers: int = 8,
) -> List[Tuple[CodeBlock, float]]:
    """
    Stage 2: Score symbols across Stage-1 files using TypeSafe Jev while prioritizing
    functions that have callers or are substantive exported entrypoints.
    """
    import re
    api_key = os.environ.get("TYPESAFE_API_KEY")
    if not api_key:
        raise ValueError("TYPESAFE_API_KEY is not set.")

    if not blocks:
        return []

    keywords = extract_intent_keywords(task_intent)

    by_file: Dict[str, List[CodeBlock]] = {}
    for blk in blocks:
        by_file.setdefault(str(blk.file_path), []).append(blk)

    intra_file_called: Dict[Tuple[str, str], int] = {}
    for f_key, f_blocks in by_file.items():
        if f_key.endswith(".md"):
            continue
        for b in f_blocks:
            if b.kind not in {"function", "method", "class"} or len(b.name) < 5:
                continue
            pat = re.compile(rf"\b{re.escape(b.name)}\s*\(")
            cnt = sum(1 for other in f_blocks if other.name != b.name and pat.search(other.content))
            if cnt > 0:
                intra_file_called[(f_key, b.name)] = cnt

    wants_types = any(w in task_intent.lower() for w in ("type", "interface", "schema", "타입", "인터페이스", "스키마"))
    candidates_for_jev: List[CodeBlock] = []
    for f_key, f_blocks in by_file.items():
        is_doc = f_key.endswith(".md")
        func_blocks = [b for b in f_blocks if b.kind in {"function", "method", "class", "doc_section"}]
        pool = func_blocks if (func_blocks and not wants_types) else f_blocks
        per_file_cap = 5 if is_doc else 9
        if len(pool) > per_file_cap:
            substantive = [
                b for b in pool
                if (b.end_line - b.start_line + 1) >= 7
                or b.signature.startswith("export ")
                or intra_file_called.get((f_key, b.name), 0) > 0
            ]
            if not substantive:
                substantive = pool
            substantive.sort(
                key=lambda b: _structural_priority(b, keywords, intra_file_called.get((f_key, b.name), 0)),
                reverse=True,
            )
            candidates_for_jev.extend(substantive[:per_file_cap])
        else:
            candidates_for_jev.extend(pool)

    if len(candidates_for_jev) > 68:
        candidates_for_jev = candidates_for_jev[:68]

    chunk_size = 24
    batches = [candidates_for_jev[i : i + chunk_size] for i in range(0, len(candidates_for_jev), chunk_size)]

    scored_blocks: List[Tuple[CodeBlock, float]] = []

    def process_batch(batch: List[CodeBlock]):
        questions = {}
        idx_to_block = {}
        for idx, block in enumerate(batch):
            q_id = f"s_{idx}"
            idx_to_block[q_id] = block
            fname = block.file_path.name
            safe_summary = block.summary.replace("\n", " ")[:220]
            questions[q_id] = {
                "type": "noul",
                "instructions": (
                    f"Is '{block.name}' in {fname} ({block.signature}) directly relevant to the task: '{task_intent}'? "
                    f"Summary: {safe_summary}"
                ),
            }

        body = {
            "model": os.environ.get("TYPESAFE_MODEL", "jev-latest"),
            "state": {
                "task": task_intent,
                "batch_symbol_count": len(batch),
            },
            "questions": questions,
        }

        res = _call_jev_single(body, api_key)
        answers = res.get("answers", {})
        results = []
        for q_id, blk in idx_to_block.items():
            raw_jev = float(answers.get(q_id, {}).get("noul", 0.0))
            results.append((blk, raw_jev))
        return results

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(process_batch, b) for b in batches]
        for f in concurrent.futures.as_completed(futures):
            try:
                scored_blocks.extend(f.result())
            except Exception:
                pass

    scored_by_file: Dict[str, List[Tuple[CodeBlock, float]]] = {}
    for blk, sc in scored_blocks:
        scored_by_file.setdefault(str(blk.file_path), []).append((blk, sc))

    code_selected: List[Tuple[CodeBlock, float]] = []
    doc_selected: List[Tuple[CodeBlock, float]] = []

    for f_key, f_list in scored_by_file.items():
        is_doc = f_key.endswith(".md")
        file_cap = 3 if is_doc else max_per_file

        def rank_key(item: Tuple[CodeBlock, float]):
            b, sc = item
            span = b.end_line - b.start_line + 1
            c_cnt = intra_file_called.get((f_key, b.name), 0)
            is_exp = b.signature.startswith("export ") and b.kind in {"function", "method", "class"}
            is_core_fn = (c_cnt > 0 or is_exp) and span >= 8
            passes = sc >= threshold or (is_core_fn and sc >= threshold - 0.15)
            return (
                1 if passes else 0,
                1 if is_core_fn else 0,
                _structural_priority(b, keywords, c_cnt),
                round(sc, 3),
            )

        f_list.sort(key=rank_key, reverse=True)
        selected_for_file: List[Tuple[CodeBlock, float]] = []
        for blk_i, sc_i in f_list[:file_cap]:
            span_i = blk_i.end_line - blk_i.start_line + 1
            c_cnt = intra_file_called.get((f_key, blk_i.name), 0)
            is_exp = blk_i.signature.startswith("export ") and blk_i.kind in {"function", "method", "class"}
            if sc_i >= threshold or ((c_cnt > 0 or is_exp) and span_i >= 8):
                selected_for_file.append((blk_i, max(sc_i, threshold)))

        if not selected_for_file and f_list:
            selected_for_file.append((f_list[0][0], max(f_list[0][1], threshold)))

        if is_doc:
            doc_selected.extend(selected_for_file[:3])
        else:
            code_selected.extend(selected_for_file[:max_per_file])

    doc_selected.sort(key=lambda x: x[1], reverse=True)
    code_selected.sort(key=lambda x: x[1], reverse=True)
    doc_kept = doc_selected[:12]
    code_kept = code_selected[: max(1, max_blocks - len(doc_kept))]
    return code_kept + doc_kept
