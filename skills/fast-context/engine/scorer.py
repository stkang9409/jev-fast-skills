"""TypeSafe Jev two-stage parallel relevance scorer with strict Top-K caps & IDF lexical pre-filtering."""

import os
import time
import concurrent.futures
from pathlib import Path
from typing import List, Dict, Tuple, Any
import httpx
from dotenv import load_dotenv

from .parser import CodeBlock
from .scanner import extract_intent_keywords

load_dotenv(dotenv_path=Path(__file__).resolve().parents[3] / ".env")
load_dotenv(dotenv_path=Path.home() / ".gemini" / ".env")
load_dotenv()

CLIENT = httpx.Client(
    http2=True,
    timeout=12,
    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
)


def _call_jev_single(body: Dict[str, Any], api_key: str, retries: int = 2) -> Dict[str, Any]:
    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = CLIENT.post(
                "https://api.typesafe.ai/v1/systemone",
                json=body,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if not resp.is_error:
                return resp.json()
            last_err = RuntimeError(f"TypeSafe Jev returned {resp.status_code}: {resp.text}")
        except Exception as e:
            last_err = e
        if attempt < retries:
            time.sleep(0.12 * (attempt + 1))
    raise last_err


def score_files(
    task_intent: str,
    file_summaries: Dict[str, str],
    threshold: float = 0.45,
    max_files: int = 10,
    max_workers: int = 5,
) -> List[Tuple[str, float]]:
    """Stage 1: Score candidate files via parallel Jev noul questions and cap at top `max_files`."""
    api_key = os.environ.get("TYPESAFE_API_KEY")
    if not api_key:
        raise ValueError("TYPESAFE_API_KEY is not set.")

    items = list(file_summaries.items())
    if not items:
        return []

    intent_lower = task_intent.lower()
    chunk_size = 15
    batches = [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]

    scored_files: List[Tuple[str, float]] = []

    def process_batch(batch):
        questions = {}
        idx_to_path = {}
        for idx, (path, preview) in enumerate(batch):
            q_id = f"q_{idx}"
            idx_to_path[q_id] = path
            safe_preview = preview.replace("\n", " ")[:180]
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
            score = float(answers.get(q_id, {}).get("noul", 0.0))
            preview_str = file_summaries.get(path, "")
            stem = Path(path).stem.lower()
            fname = Path(path).name.lower()
            if (fname not in {"skill.md", "readme.md", "index.ts", "index.js", "index.cjs", "page.tsx"}) and (
                fname in intent_lower or (len(stem) >= 5 and stem in intent_lower)
            ):
                score = max(score, 0.92)
            elif "KeywordHits(" in preview_str:
                import re as _re
                m_hit = _re.search(r"KeywordHits\((\d+)\)", preview_str)
                n_hits = int(m_hit.group(1)) if m_hit else 1
                boost = min(0.48, 0.12 + 0.05 * n_hits)
                if n_hits >= 4:
                    score = max(score, 0.62)
                score = min(0.95, score + boost)
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


def score_symbols(
    task_intent: str,
    blocks: List[CodeBlock],
    threshold: float = 0.42,
    max_blocks: int = 18,
    max_candidates_for_jev: int = 45,
    max_workers: int = 5,
) -> List[Tuple[CodeBlock, float]]:
    """Stage 2: Pre-rank blocks lexically if >45, score via Jev, and cap at top `max_blocks`."""
    api_key = os.environ.get("TYPESAFE_API_KEY")
    if not api_key:
        raise ValueError("TYPESAFE_API_KEY is not set.")

    if not blocks:
        return []

    keywords = extract_intent_keywords(task_intent)

    # Pre-filter candidate blocks with per-file cap (max 7 blocks per file) so 1 huge file never starves others
    ranked_pre = []
    for blk in blocks:
        c_low = blk.content.lower()
        n_low = blk.name.lower()
        hit_score = sum((2.5 if kw in n_low else 1.0) for kw in keywords if kw in n_low or kw in c_low)
        if blk.kind in {"function", "method", "class", "ipc", "doc_section"}:
            hit_score += 0.5
        ranked_pre.append((hit_score, blk))
    ranked_pre.sort(key=lambda x: x[0], reverse=True)

    per_file_pre: Dict[str, int] = {}
    diversified_pre: List[CodeBlock] = []
    for _, blk in ranked_pre:
        f_key = str(blk.file_path)
        if per_file_pre.get(f_key, 0) < 7:
            diversified_pre.append(blk)
            per_file_pre[f_key] = per_file_pre.get(f_key, 0) + 1
        if len(diversified_pre) >= max_candidates_for_jev:
            break
    blocks = diversified_pre

    chunk_size = 15
    batches = [blocks[i : i + chunk_size] for i in range(0, len(blocks), chunk_size)]

    scored_blocks: List[Tuple[CodeBlock, float]] = []

    def process_batch(batch: List[CodeBlock]):
        questions = {}
        idx_to_block = {}
        for idx, block in enumerate(batch):
            q_id = f"s_{idx}"
            idx_to_block[q_id] = block
            fname = block.file_path.name
            safe_summary = block.summary.replace("\n", " ")[:150]
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
            score = float(answers.get(q_id, {}).get("noul", 0.0))
            content_lower = blk.content.lower()
            name_lower = blk.name.lower()
            name_hits = [kw for kw in keywords if len(kw) >= 3 and kw in name_lower]
            hits = [kw for kw in keywords if kw in content_lower or kw in name_lower]
            if name_hits:
                score = max(score, min(0.96, 0.72 + 0.08 * len(name_hits)))
            elif hits and blk.kind in {"function", "method", "class", "ipc", "doc_section", "file", "type"}:
                score = max(score, min(0.92, score + 0.10 * len(hits)))
            results.append((blk, score))
        return results

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(process_batch, b) for b in batches]
        for f in concurrent.futures.as_completed(futures):
            try:
                scored_blocks.extend(f.result())
            except Exception:
                pass

    scored_blocks.sort(key=lambda x: x[1], reverse=True)
    filtered = []
    per_file_post: Dict[str, int] = {}
    for blk, sc in scored_blocks:
        if sc < threshold:
            continue
        f_key = str(blk.file_path)
        if per_file_post.get(f_key, 0) < 4:
            filtered.append((blk, sc))
            per_file_post[f_key] = per_file_post.get(f_key, 0) + 1
        if len(filtered) >= max_blocks:
            break

    if not filtered and scored_blocks:
        filtered = scored_blocks[:3]
    return filtered
