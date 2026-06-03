import logging
from typing import Any

from backend.memory.long_term import LongTermMemory
from backend.memory.compaction import CompactionEngine
from backend.memory.search.hybrid import HybridSearchEngine

logger = logging.getLogger("geekcat.memory")


class MemoryManager:
    """Central memory orchestrator.

    Integrates:
      - Long-term memory (Chroma + BM25 via LongTermMemory)
      - Adaptive compaction with multi-stage summarisation
      - Pre-compaction memory flush
      - Hybrid search (vector + keyword + RRF)
    """

    def __init__(
        self,
        ltm: LongTermMemory,
        compaction: CompactionEngine,
        hybrid_search: HybridSearchEngine,
    ):
        self.ltm = ltm
        self.compaction = compaction
        self.hybrid_search = hybrid_search

    # ── Pre-Compaction Memory Flush ──

    def flush_before_compaction(
        self,
        user_id: str,
        messages: list,
    ) -> int:
        """Extract important facts from conversation and persist to LTM
        before compaction potentially destroys them."""
        facts = self.compaction.flush_important_facts(messages)
        saved = 0
        for fact in facts:
            self.ltm.save(
                user_id,
                fact,
                metadata={"type": "pre_compaction_flush", "source": "auto_extract"},
            )
            saved += 1
        if saved:
            logger.info("pre_compaction_flush: user=%s facts=%d", user_id, saved)
        return saved

    # ── Adaptive Compaction ──

    def compact_if_needed(self, messages: list, context_window: int = 128_000) -> list:
        """Adaptive compaction triggered when token threshold is exceeded."""
        return self.compaction.compact(messages, context_window=context_window)

    # ── Hybrid Memory Retrieval ──

    def retrieve(
        self,
        user_id: str,
        query: str,
        k: int = 5,
    ) -> list[dict[str, Any]]:
        """Retrieve relevant memories using hybrid search (vector + BM25 + RRF)."""
        return self.hybrid_search.hybrid_search(user_id, query, k=k)

    def retrieve_global(
        self,
        collection_name: str,
        query: str,
        k: int = 5,
    ) -> list[dict[str, Any]]:
        """Retrieve from a global collection (e.g. pod_catalog)."""
        return self.hybrid_search.hybrid_search_global(collection_name, query, k=k)

    # ── Convenience wrappers ──

    def save_memory(self, user_id: str, text: str, metadata: dict | None = None) -> str:
        return self.ltm.save(user_id, text, metadata)

    def save_brand_rule(self, user_id: str, key: str, value: str) -> str:
        return self.ltm.save_brand_rule(user_id, key, value)

    def get_brand_rules(self, user_id: str) -> dict[str, str]:
        return self.ltm.get_brand_rules(user_id)

    def list_memories(self, user_id: str, limit: int = 100) -> list[dict[str, Any]]:
        return self.ltm.list_all(user_id, limit=limit)

    def delete_memory(self, user_id: str, doc_id: str) -> None:
        self.ltm.delete(user_id, doc_id)

    def count_memories(self, user_id: str) -> int:
        return self.ltm.count(user_id)

    def format_memories_for_prompt(self, user_id: str, query: str, k: int = 5) -> str:
        """Format relevant memories into a bullet list for system prompt injection."""
        return self.ltm.format_memories_for_prompt(user_id, query, k=k)

    def save_analytics(self, user_id: str, event_type: str, payload: dict | None = None):
        return self.ltm.save_analytics_event(user_id, event_type, payload)

    def rebuild_bm25_index(self, user_id: str) -> None:
        self.ltm.rebuild_bm25(user_id)
