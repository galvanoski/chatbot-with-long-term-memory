import logging
from typing import Any

from backend.rag.vectorstore import ChromaStore
from backend.memory.search.sqlite_fts5 import SQLiteFTS5

logger = logging.getLogger("geekcat.hybrid_search")

RRF_K = 60  # RRF constant


class HybridSearchEngine:
    """Hybrid search combining semantic (Chroma vector) and keyword
    (SQLite FTS5 BM25) retrieval with Reciprocal Rank Fusion (RRF)."""

    def __init__(
        self,
        vector_store: ChromaStore,
        bm25: SQLiteFTS5,
        rrf_constant: int = RRF_K,
    ):
        self.vector_store = vector_store
        self.bm25 = bm25
        self.rrf_k = rrf_constant

    def hybrid_search(
        self,
        user_id: str,
        query: str,
        k: int = 5,
        vector_k: int | None = None,
        keyword_k: int | None = None,
    ) -> list[dict[str, Any]]:
        """Search using both vectors and BM25, fused via RRF.

        Args:
            user_id: Target user.
            query: Search query.
            k: Final number of results (after RRF fusion).
            vector_k: Candidates from vector search (default: k * 2).
            keyword_k: Candidates from keyword search (default: k * 2).

        Returns:
            List of result dicts with keys: id, text, score (RRF), source.
        """
        vector_k = vector_k or k * 2
        keyword_k = keyword_k or k * 2

        # 1. Semantic search via Chroma
        vector_results = self.vector_store.search_user_memories(user_id, query, k=vector_k)

        # 2. Keyword search via SQLite FTS5
        keyword_results = self.bm25.search(user_id, query, k=keyword_k)

        # 3. RRF fusion
        return self._reciprocal_rank_fusion(
            vector_results=vector_results,
            keyword_results=keyword_results,
            k=k,
        )

    def hybrid_search_global(
        self,
        collection_name: str,
        query: str,
        k: int = 5,
    ) -> list[dict[str, Any]]:
        """Hybrid search over a global collection (e.g. pod_catalog).

        Note: BM25 search over global collections is not yet supported
        with separate FTS tables — uses vector search only for globals.
        """
        return self.vector_store.search_global(collection_name, query, k=k)

    def _reciprocal_rank_fusion(
        self,
        vector_results: list[dict[str, Any]],
        keyword_results: list[tuple[str, float, str]],
        k: int,
    ) -> list[dict[str, Any]]:
        """Fuse two ranked result lists using RRF."""
        rrf_scores: dict[str, float] = {}
        result_map: dict[str, dict[str, Any]] = {}

        # Process vector results (ranked by distance, ascending)
        for rank, item in enumerate(vector_results):
            doc_id = item["id"]
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + 1.0 / (self.rrf_k + rank + 1)
            if doc_id not in result_map:
                result_map[doc_id] = {**item, "source": "vector"}

        # Process keyword results (ranked by score, descending)
        for rank, (doc_id, score, text) in enumerate(keyword_results):
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + 1.0 / (self.rrf_k + rank + 1)
            if doc_id not in result_map:
                result_map[doc_id] = {
                    "id": doc_id,
                    "text": text,
                    "score": score,
                    "source": "keyword",
                }
            else:
                result_map[doc_id]["source"] = "hybrid"

        # Sort by RRF score descending
        sorted_ids = sorted(rrf_scores.keys(), key=lambda i: rrf_scores[i], reverse=True)

        results = []
        for doc_id in sorted_ids[:k]:
            item = result_map[doc_id]
            item["rrf_score"] = round(rrf_scores[doc_id], 4)
            results.append(item)

        return results
