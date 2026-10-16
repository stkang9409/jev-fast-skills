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
    return filtered[:max_files]


def _structural_priority(blk: CodeBlock, keywords: List[str]) -> float:
    """
    Domain-agnostic structural priority used ONLY when a single file has >18 blocks
    or as a tie-breaker between identical Jev scores.
    """
    span = max(1, blk.end_line - blk.start_line + 1)
    is_exported = blk.signature.startswith("export ")
    prio = 0.0
    if is_exported:
        prio += 2.0
    if blk.kind in {"function", "method", "ipc", "doc_section"}:
        prio += 1.5
    elif blk.kind == "type":
        prio -= 1.0
    if span >= 8:
        prio += 1.5
    elif span <= 5 and not is_exported:
        prio -= 2.0
    s_low = blk.summary.lower()
    n_low = blk.name.lower()
    for kw in keywords:
        if len(kw) >= 2 and kw in n_low:
            prio += 2.0
        elif kw in s_low:
            prio += 1.0
    return prio


def score_symbols(
    task_intent: str,
    blocks: List[CodeBlock],
    threshold: float = 0.60,
    max_blocks: int = 75,
    max_per_file: int = 4,
    max_workers: int = 8,
) -> List[Tuple[CodeBlock, float]]:
    """
    Stage 2: Score symbols across Stage-1 files using Pure TypeSafe Jev multilingual semantic evaluation.
    """
    api_key = os.environ.get("TYPESAFE_API_KEY")
    if not api_key:
        raise ValueError("TYPESAFE_API_KEY is not set.")

    if not blocks:
        return []

    keywords = extract_intent_keywords(task_intent)

    by_file: Dict[str, List[CodeBlock]] = {}
    for blk in blocks:
        by_file.setdefault(str(blk.file_path), []).append(blk)

    # Structural pre-filter for large files (>14 symbols): keep exported & >=6-line functions, cap at 18 per file
    candidates_for_jev: List[CodeBlock] = []
    for f_key, f_blocks in by_file.items():
        if len(f_blocks) > 14:
            substantive = [
                b for b in f_blocks
                if (b.end_line - b.start_line + 1) >= 6 or b.signature.startswith("export ")
            ]
            if not substantive:
                substantive = f_blocks
            substantive.sort(key=lambda b: _structural_priority(b, keywords), reverse=True)
            candidates_for_jev.extend(substantive[:32])
        else:
            candidates_for_jev.extend(f_blocks)

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

    filtered: List[Tuple[CodeBlock, float]] = []
    for f_key, f_list in scored_by_file.items():
        f_list.sort(
            key=lambda x: (round(x[1], 3), _structural_priority(x[0], keywords)),
            reverse=True,
        )
        above = [item for item in f_list if item[1] >= threshold]
        if above:
            limit = max_per_file + 1 if len(above) >= 5 and above[4][1] >= 0.61 else max_per_file
            filtered.extend(above[:limit])
        elif f_list:
            best_blk, best_sc = f_list[0]
            if best_sc >= threshold - 0.12:
                filtered.append((best_blk, max(best_sc, threshold)))

    filtered.sort(key=lambda x: x[1], reverse=True)
    return filtered[:max_blocks]
