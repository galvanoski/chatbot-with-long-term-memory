"""RAG pipeline: query expansion → search → re-rank → context compression."""

import json
import logging
import os
import re
import time
from typing import Any, Callable

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI

logger = logging.getLogger("geekcat.rag.pipeline")

# ── Helpers ─────────────────────────────────────────────

_llm: ChatOpenAI | None = None


def _get_llm() -> ChatOpenAI:
    global _llm
    if _llm is None:
        _llm = ChatOpenAI(
            model="openai/gpt-4o-mini",
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
        )
    return _llm


def _parse_json(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{[\s\S]*\}", text)
        return json.loads(match.group(0)) if match else None


# ── Step 1: Query Expansion ────────────────────────────

EXPANSION_PROMPT = (
    "You are a search query expansion assistant for an e-commerce POD store (The Geek Cat). "
    "Given a user's search query, generate 3 alternative query variants that "
    "capture different phrasings, synonyms, and related concepts to improve recall.\n\n"
    "Return ONLY valid JSON: {{\"queries\": [\"variant1\", \"variant2\", \"variant3\"]}}\n"
    "Each variant must be a single search string, under 100 characters."
)


def expand_query(query: str, n_variants: int = 3) -> list[str]:
    """Generate query variants using the LLM."""
    queries = [query]
    if not query or len(query) < 3:
        return queries
    try:
        start = time.time()
        llm = _get_llm()
        resp = llm.invoke([
            SystemMessage(content=EXPANSION_PROMPT),
            HumanMessage(content=f"Original query: {query}"),
        ])
        elapsed = (time.time() - start) * 1000
        payload = _parse_json(resp.content if hasattr(resp, "content") else str(resp))
        if payload and isinstance(payload.get("queries"), list):
            variants = [str(q).strip() for q in payload["queries"] if str(q).strip()]
            queries.extend(variants[:n_variants])
            logger.debug("expand_query: %d variants in %.0fms", len(variants), elapsed)
    except Exception as exc:
        logger.warning("expand_query failed: %s", exc)
    return queries


# ── Step 2: Re-ranking ─────────────────────────────────

RERANK_PROMPT = (
    "You are a search relevance judge. Given a query and a list of documents, "
    "assign each document a relevance score from 0.0 (totally irrelevant) to 1.0 (perfect match).\n\n"
    "Output ONLY valid JSON: {{\"scores\": [{{\"index\": 0, \"score\": 0.95, \"reason\": \"...\"}}, ...]}}\n"
    "Scores must be sorted descending by score."
)


def rerank_documents(query: str, documents: list[dict[str, Any]], top_k: int | None = None) -> list[dict[str, Any]]:
    """Re-rank documents by relevance using the LLM."""
    if not documents or len(documents) <= 1:
        return documents[:top_k] if top_k else documents
    try:
        doc_lines = []
        for i, doc in enumerate(documents):
            text = (doc.get("text") or "")[:500]
            doc_lines.append(f"{i}. {text}")
        docs_text = "\n".join(doc_lines)

        start = time.time()
        llm = _get_llm()
        resp = llm.invoke([
            SystemMessage(content=RERANK_PROMPT),
            HumanMessage(content=f"Query: {query}\n\nDocuments:\n{docs_text}"),
        ])
        elapsed = (time.time() - start) * 1000
        payload = _parse_json(resp.content if hasattr(resp, "content") else str(resp))
        if payload and isinstance(payload.get("scores"), list):
            scores = payload["scores"]
            index_score = {}
            for entry in scores:
                idx = entry.get("index")
                score = entry.get("score", 0)
                if isinstance(idx, int) and 0 <= idx < len(documents):
                    index_score[idx] = float(score)
            ranked = sorted(
                documents,
                key=lambda d, idx_score=index_score: idx_score.get(documents.index(d), 0),
                reverse=True,
            )
            logger.debug("rerank: %d docs in %.0fms", len(documents), elapsed)
            for doc in ranked:
                idx = documents.index(doc)
                doc["rerank_score"] = index_score.get(idx, 0)
            return ranked[:top_k] if top_k else ranked
    except Exception as exc:
        logger.warning("rerank failed: %s", exc)
    return documents[:top_k] if top_k else documents


# ── Step 3: Context Compression ────────────────────────

COMPRESS_PROMPT = (
    "You are a document compression assistant. Given a user query and a document, "
    "extract ONLY the sentences and facts that are directly relevant to answering the query. "
    "Remove irrelevant information, preserve key data (numbers, names, dates, prices, SKUs, URLs). "
    "Keep the original language. Return the compressed text.\n\n"
    "Output ONLY the compressed text, no explanations."
)


def compress_document(query: str, text: str, max_chars: int = 500) -> str:
    """Compress a single document to only query-relevant content using the LLM."""
    if not text or len(text) < 100:
        return text[:max_chars]
    try:
        start = time.time()
        llm = _get_llm()
        resp = llm.invoke([
            SystemMessage(content=COMPRESS_PROMPT),
            HumanMessage(content=f"Query: {query}\n\nDocument:\n{text[:1500]}"),
        ])
        elapsed = (time.time() - start) * 1000
        compressed = (resp.content if hasattr(resp, "content") else str(resp)).strip()
        logger.debug("compress: %d→%d chars in %.0fms", len(text), len(compressed), elapsed)
        return compressed[:max_chars]
    except Exception as exc:
        logger.warning("compress failed: %s", exc)
        return text[:max_chars]


def compress_documents(query: str, documents: list[dict[str, Any]], max_chars: int = 500) -> list[dict[str, Any]]:
    """Compress a batch of documents in a single LLM call."""
    if not documents:
        return documents
    try:
        doc_texts = []
        for i, doc in enumerate(documents):
            text = (doc.get("text") or "")[:800]
            doc_texts.append(f"[{i}] {text}")
        combined = "\n\n".join(doc_texts)
        llm = _get_llm()
        resp = llm.invoke([
            SystemMessage(content=(
                "You are a document compression assistant. Given a user query and multiple documents, "
                "for each document extract ONLY the sentences and facts directly relevant to the query. "
                "Remove irrelevant information. Preserve key data (names, SKUs, prices, URLs).\n\n"
                "Output ONLY valid JSON: {\"compressed\": [{\"index\": 0, \"text\": \"...\"}, ...]}"
            )),
            HumanMessage(content=f"Query: {query}\n\nDocuments:\n{combined}"),
        ])
        payload = _parse_json(resp.content if hasattr(resp, "content") else str(resp))
        if payload and isinstance(payload.get("compressed"), list):
            compressed_map = {}
            for entry in payload["compressed"]:
                idx = entry.get("index")
                text = str(entry.get("text", "")).strip()
                if isinstance(idx, int) and text:
                    compressed_map[idx] = text[:max_chars]
            for i, doc in enumerate(documents):
                if i in compressed_map:
                    doc["compressed_text"] = compressed_map[i]
                else:
                    doc["compressed_text"] = (doc.get("text") or "")[:max_chars]
        else:
            for doc in documents:
                doc["compressed_text"] = (doc.get("text") or "")[:max_chars]
    except Exception as exc:
        logger.warning("batch compress failed: %s", exc)
        for doc in documents:
            doc["compressed_text"] = (doc.get("text") or "")[:max_chars]
    return documents


# ── Orchestrator ────────────────────────────────────────

def run_rag_pipeline(
    query: str,
    search_fn: Callable[[str, int], list[dict[str, Any]]],
    top_k: int = 3,
    expand: bool = True,
    rerank: bool = True,
    compress: bool = True,
) -> dict[str, Any]:
    """Run the full RAG pipeline: expand → search → rerank → compress.

    Args:
        query: User search query.
        search_fn: Callable(str query, int k) returning list[dict] with keys id, text, score, metadata.
        top_k: Final number of results to return.
        expand: Whether to expand the query first.
        rerank: Whether to re-rank results using the LLM.
        compress: Whether to compress document text using the LLM.

    Returns:
        dict with keys: results (list), queries_used (list), trace (list of stage events).
    """
    trace: list[dict[str, Any]] = []
    queries_used = [query]

    # 1. Query expansion
    if expand:
        exp_start = time.time()
        queries_used = expand_query(query)
        exp_latency = (time.time() - exp_start) * 1000
        trace.append({
            "stage": "query_expand",
            "original_query": query[:100],
            "variants": [q[:100] for q in queries_used[1:]],
            "latency_ms": round(exp_latency, 1),
        })
        logger.info("pipeline: expanded to %d queries", len(queries_used))

    # 2. Search with each variant
    seen_ids: set[str] = set()
    all_results: list[dict[str, Any]] = []
    search_start = time.time()
    for q in queries_used:
        results = search_fn(q, top_k * 3)
        for doc in results:
            doc_id = str(doc.get("id", ""))
            if doc_id and doc_id not in seen_ids:
                seen_ids.add(doc_id)
                doc["_search_query"] = q
                all_results.append(doc)
    search_latency = (time.time() - search_start) * 1000
    trace.append({
        "stage": "search_multi",
        "queries": len(queries_used),
        "candidates": len(all_results),
        "latency_ms": round(search_latency, 1),
    })

    if not all_results:
        return {"results": [], "queries_used": queries_used, "trace": trace}

    # 3. Re-ranking
    if rerank:
        rerank_start = time.time()
        reranked = rerank_documents(query, all_results, top_k=top_k * 2)
        rerank_latency = (time.time() - rerank_start) * 1000
        trace.append({
            "stage": "rerank",
            "candidates_in": len(all_results),
            "candidates_out": len(reranked),
            "latency_ms": round(rerank_latency, 1),
        })
        logger.info("pipeline: reranked %d → %d docs", len(all_results), len(reranked))
    else:
        reranked = all_results[:top_k * 2]

    # 4. Context compression
    if compress and reranked:
        comp_start = time.time()
        reranked = compress_documents(query, reranked, max_chars=400)
        comp_latency = (time.time() - comp_start) * 1000
        compressed_count = sum(1 for d in reranked if d.get("compressed_text"))
        trace.append({
            "stage": "compress",
            "docs_in": len(reranked),
            "docs_compressed": compressed_count,
            "latency_ms": round(comp_latency, 1),
        })
        logger.info("pipeline: compressed %d docs", compressed_count)

    # Use compressed_text as the primary text if available
    final = []
    for doc in reranked[:top_k]:
        final_doc = dict(doc)
        if doc.get("compressed_text"):
            final_doc["text"] = doc["compressed_text"]
        final.append(final_doc)

    return {"results": final, "queries_used": queries_used, "trace": trace}
