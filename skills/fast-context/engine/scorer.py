"""TypeSafe Jev two-stage parallel relevance scorer for files and symbols."""

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
            time.sleep(0.15 * (attempt + 1))
    raise last_err


def score_files(
    task_intent: str,
    file_summaries: Dict[str, str],
    threshold: float = 0.45,
    max_workers: int = 5,
) -> List[Tuple[str, float]]:
    """Stage 1: Score all files for relevance using parallel Jev noul questions + lexical hit anchoring."""
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
            safe_preview = preview.replace("\n", " ")[:180]
            questions[q_id] = {
                "type": "noul",
                "instructions": (
                    f"Is the file '{path}' relevant and likely needed for the task: '{task_intent}'? "
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
            # Lexical anchor boost for direct file name match or strong keyword hits in code
            stem = Path(path).stem.lower()
            fname = Path(path).name.lower()
            if (fname not in {"skill.md", "readme.md", "index.ts", "page.tsx"}) and (
                fname in intent_lower or (len(stem) >= 5 and stem in intent_lower)
            ):
                score = max(score, 0.92)
            elif "KeywordHits(" in preview_str and not path.endswith(".md"):
                score = max(score, 0.72)
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
    return filtered


def score_symbols(
    task_intent: str,
    blocks: List[CodeBlock],
    threshold: float = 0.40,
    max_workers: int = 5,
) -> List[Tuple[CodeBlock, float]]:
    """Stage 2: Score parsed symbols/blocks for relevance using parallel Jev calls + identifier hit boost."""
    api_key = os.environ.get("TYPESAFE_API_KEY")
    if not api_key:
        raise ValueError("TYPESAFE_API_KEY is not set.")

    if not blocks:
        return []

    if len(blocks) <= 3:
        return [(b, 1.0) for b in blocks]

    keywords = extract_intent_keywords(task_intent)
    chunk_size = 12
    batches = [blocks[i : i + chunk_size] for i in range(0, len(blocks), chunk_size)]

    scored_blocks: List[Tuple[CodeBlock, float]] = []

    def process_batch(batch):
        questions = {}
        idx_to_block = {}
        for idx, block in enumerate(batch):
            q_id = f"sym_{idx}"
            idx_to_block[q_id] = block
            fname = block.file_path.name
            content_lower = block.content.lower()
            hits = [kw for kw in keywords if kw in content_lower or kw in block.name.lower()]
            hit_str = f" [Contains keywords: {', '.join(hits[:6])}]" if hits else ""
            safe_summary = (block.summary.replace("\n", " ")[:90] + hit_str)[:160]
            questions[q_id] = {
                "type": "noul",
                "instructions": (
                    f"Is '{block.name}' in {fname} ({block.signature}) relevant to the task: '{task_intent}'? "
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
            hits = [kw for kw in keywords if kw in content_lower or kw in blk.name.lower()]
            if hits and blk.kind in {"function", "method", "class"}:
                score = max(score, min(0.95, 0.55 + 0.12 * len(hits)))
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
    filtered = [item for item in scored_blocks if item[1] >= threshold]

    if not filtered and scored_blocks:
        filtered = scored_blocks[:3]
    return filtered
