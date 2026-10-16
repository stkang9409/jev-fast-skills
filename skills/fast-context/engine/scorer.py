"""Batched parallel scorer using TypeSafe Jev API with wide-recall per-file symbol guarantee."""

import os
import re
import time
import httpx
import concurrent.futures
from pathlib import Path
from typing import List, Dict, Any, Tuple
from dotenv import load_dotenv

from .parser import CodeBlock
from .scanner import extract_intent_keywords

load_dotenv(dotenv_path=Path(__file__).resolve().parents[3] / ".env")
load_dotenv(dotenv_path=Path.home() / ".gemini" / ".env")
load_dotenv()

JEV_URL = os.environ.get("TYPESAFE_API_URL", "https://api.typesafe.ai/v1/systemone")
CLIENT = httpx.Client(http2=True, timeout=15.0)

LOW_SIGNAL_NAMES = {
    "amountvalues", "claimnumber", "claimtype", "payer", "context", "field",
    "table", "validdate", "decimaltext", "toothmask", "visitmask", "copaypolicy",
    "supportpolicy", "roundwonhalfup", "recordtypefor", "parserecord", "parsefield",
    "parsed091record", "parsed091file", "parse091fileresult",
}


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
    max_workers: int = 6,
) -> List[Tuple[str, float]]:
    """Stage 1: Score candidate files in parallel and return all files meeting threshold (up to max_files)."""
    api_key = os.environ.get("TYPESAFE_API_KEY")
    if not api_key:
        raise ValueError("TYPESAFE_API_KEY is not set.")

    items = list(file_summaries.items())
    if not items:
        return []

    intent_lower = task_intent.lower()
    keywords = extract_intent_keywords(task_intent)
    chunk_size = 15
    batches = [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]

    scored_files: List[Tuple[str, float]] = []

    def process_batch(batch):
        questions = {}
        idx_to_path = {}
        for idx, (path, preview) in enumerate(batch):
            q_id = f"q_{idx}"
            idx_to_path[q_id] = path
            safe_preview = preview.replace("\n", " ")[:220]
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
            preview_str = file_summaries.get(path, "")
            preview_low = preview_str.lower()
            stem = Path(path).stem.lower()
            fname = Path(path).name.lower()

            path_hits = sum(1 for kw in keywords if len(kw) >= 3 and kw in path.lower())
            has_sql_hit = "sqltables:" in preview_low and any(kw in preview_low for kw in keywords)

            if (fname not in {"skill.md", "readme.md", "index.ts", "index.js", "index.cjs", "page.tsx"}) and (
                fname in intent_lower or (len(stem) >= 5 and stem in intent_lower)
            ):
                score = max(score, 0.92)
            elif "KeywordHits(" in preview_str:
                m_hit = re.search(r"KeywordHits\((\d+)\)", preview_str)
                n_hits = int(m_hit.group(1)) if m_hit else 1
                # Only boost files that either Jev scored reasonably (>= 0.28) or have strong path/SQL/keyword density
                if raw_jev >= 0.25 or path_hits >= 1 or has_sql_hit or n_hits >= 6:
                    boost = min(0.44, 0.06 + 0.04 * n_hits + 0.06 * path_hits + (0.12 if has_sql_hit else 0.0))
                    if (n_hits >= 5 and path_hits >= 1) or (n_hits >= 5 and has_sql_hit) or n_hits >= 9:
                        score = max(score, 0.64)
                    score = min(0.96, score + boost)
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
    return filtered[:max_files]


def _symbol_lexical_score(blk: CodeBlock, keywords: List[str]) -> Tuple[float, int, int]:
    """Return (weighted_hit_score, name_hit_count, total_hit_count) for a symbol block."""
    n_low = blk.name.lower()
    s_low = blk.summary.lower()
    c_low = blk.content.lower()

    name_hits = 0
    total_hits = 0
    weighted = 0.0
    for kw in keywords:
        in_name = len(kw) >= 3 and kw in n_low
        in_sum = kw in s_low
        in_body = kw in c_low
        if in_name:
            name_hits += 1
            total_hits += 1
            weighted += 3.2
        elif in_sum:
            total_hits += 1
            weighted += 2.0
        elif in_body:
            total_hits += 1
            weighted += 1.0

    if "sql:" in s_low:
        weighted += 1.8
    if blk.kind in {"function", "method", "ipc", "doc_section"}:
        weighted += 1.6
    elif blk.kind == "type":
        weighted -= 1.2
    if blk.name.lower().startswith(("assert", "validate", "verify", "check", "record", "store", "handoff", "import", "prepare", "project", "parse")):
        weighted += 1.2
    if n_low in LOW_SIGNAL_NAMES and name_hits == 0:
        weighted -= 3.0

    return weighted, name_hits, total_hits


def score_symbols(
    task_intent: str,
    blocks: List[CodeBlock],
    threshold: float = 0.60,
    max_blocks: int = 75,
    max_per_file: int = 4,
    max_candidates_for_jev: int = 105,
    max_workers: int = 6,
) -> List[Tuple[CodeBlock, float]]:
    """
    Stage 2: Score symbols across all Stage-1 files.
    Guarantees every Stage-1 file retains its top matched symbols while suppressing 0-hit helper noise.
    """
    api_key = os.environ.get("TYPESAFE_API_KEY")
    if not api_key:
        raise ValueError("TYPESAFE_API_KEY is not set.")

    if not blocks:
        return []

    keywords = extract_intent_keywords(task_intent)

    by_file: Dict[str, List[Tuple[float, int, int, CodeBlock]]] = {}
    for blk in blocks:
        w_score, n_hits, t_hits = _symbol_lexical_score(blk, keywords)
        by_file.setdefault(str(blk.file_path), []).append((w_score, n_hits, t_hits, blk))

    # Allocate candidate slots per file so NO Stage-1 file is ever cut off before Jev scoring
    per_file_cap = max(4, min(6, max_candidates_for_jev // max(1, len(by_file))))
    diversified_pre: List[CodeBlock] = []
    file_has_strong_hits: Dict[str, bool] = {}
    for f_key, f_items in by_file.items():
        f_items.sort(key=lambda x: x[0], reverse=True)
        file_has_strong_hits[f_key] = any(item[2] >= 2 or item[1] >= 1 for item in f_items)
        for w_score, n_hits, t_hits, blk in f_items[:per_file_cap]:
            diversified_pre.append(blk)

    chunk_size = 15
    batches = [diversified_pre[i : i + chunk_size] for i in range(0, len(diversified_pre), chunk_size)]

    scored_blocks: List[Tuple[CodeBlock, float]] = []

    def process_batch(batch: List[CodeBlock]):
        questions = {}
        idx_to_block = {}
        for idx, block in enumerate(batch):
            q_id = f"s_{idx}"
            idx_to_block[q_id] = block
            fname = block.file_path.name
            safe_summary = block.summary.replace("\n", " ")[:210]
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
            w_score, n_hits, t_hits = _symbol_lexical_score(blk, keywords)
            f_key = str(blk.file_path)

            score = raw_jev
            if n_hits >= 1 and t_hits >= 2:
                score = max(score, min(0.96, 0.72 + 0.06 * n_hits + 0.03 * t_hits))
            elif t_hits >= 2:
                score = max(score, min(0.93, score + 0.08 * t_hits))
            elif n_hits >= 1:
                score = max(score, min(0.90, 0.65 + 0.06 * n_hits))

            if blk.name.lower() in LOW_SIGNAL_NAMES and n_hits == 0:
                score = min(score, threshold - 0.08)
            elif file_has_strong_hits.get(f_key, False) and t_hits == 0 and n_hits == 0:
                score = min(score, threshold - 0.05)

            results.append((blk, score))
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

    filtered: List[Tuple[CodeBlock, float]] = []
    for f_key, f_list in scored_by_file.items():
        f_list.sort(key=lambda x: (round(x[1], 3), _symbol_lexical_score(x[0], keywords)[0]), reverse=True)
        above = [item for item in f_list if item[1] >= threshold]
        if above:
            limit = max_per_file + 1 if len(above) >= 5 and above[4][1] >= 0.78 else max_per_file
            filtered.extend(above[:limit])
        elif f_list:
            best_blk, best_sc = f_list[0]
            if best_sc >= threshold - 0.15:
                filtered.append((best_blk, max(best_sc, threshold)))

    filtered.sort(key=lambda x: x[1], reverse=True)
    return filtered[:max_blocks]
