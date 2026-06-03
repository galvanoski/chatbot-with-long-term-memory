from typing import Any

from backend.rag.vectorstore import ChromaStore
from backend.memory.search.sqlite_fts5 import SQLiteFTS5


class LongTermMemory:
    """Persistent long-term memory backed by Chroma (semantic) + SQLite FTS5
    (keyword). Each user_id gets an isolated collection in Chroma and an
    isolated FTS virtual table."""

    def __init__(
        self,
        vector_store: ChromaStore,
        bm25: SQLiteFTS5 | None = None,
    ):
        self.vector_store = vector_store
        self.bm25 = bm25 or SQLiteFTS5()

    # ── Write ──

    def save(
        self,
        user_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Persist a memory. Returns the document id."""
        doc_id = self.vector_store.add_memory(user_id, text, metadata)
        # Also index in BM25
        self.bm25.index(user_id, doc_id, text)
        return doc_id

    def save_brand_rule(self, user_id: str, key: str, value: str) -> str:
        """Save a brand/style rule under a known key (tone, hashtags, etc.)."""
        return self.save(user_id, value, metadata={"type": "brand_rule", "key": key})

    def save_analytics_event(
        self, user_id: str, event_type: str, payload: dict | None = None
    ) -> str:
        return self.save(
            user_id,
            f"analytics:{event_type}",
            metadata={"type": "analytics", "event": event_type, **(payload or {})},
        )

    # ── Read ──

    def search(
        self,
        user_id: str,
        query: str,
        k: int = 5,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Semantic search over user's memories (Chroma)."""
        return self.vector_store.search_user_memories(user_id, query, k=k, where=where)

    def search_keyword(
        self,
        user_id: str,
        query: str,
        k: int = 5,
    ) -> list[tuple[str, float, str]]:
        """Keyword search over user's memories (BM25 FTS5)."""
        return self.bm25.search(user_id, query, k=k)

    def get_brand_rules(self, user_id: str) -> dict[str, str]:
        """Load all brand rules for a user as a flat dict."""
        results = self.vector_store.search_user_memories(
            user_id,
            query="brand rule tone style",
            k=20,
            where={"type": "brand_rule"},
        )
        rules: dict[str, str] = {}
        for r in results:
            meta = r.get("metadata", {})
            key = meta.get("key")
            if key:
                rules[key] = r["text"]
        return rules

    def list_all(self, user_id: str, limit: int = 100) -> list[dict[str, Any]]:
        return self.vector_store.list_memories(user_id, limit=limit)

    def count(self, user_id: str) -> int:
        return self.vector_store.count_memories(user_id)

    # ── Delete ──

    def delete(self, user_id: str, doc_id: str) -> None:
        self.vector_store.delete_memory(user_id, doc_id)
        self.bm25.delete(user_id, doc_id)

    # ── Maintenance ──

    def rebuild_bm25(self, user_id: str) -> None:
        """Rebuild the FTS index for this user from Chroma data."""
        memories = self.vector_store.list_memories(user_id, limit=10_000)
        items = [(m["id"], m["text"]) for m in memories if m.get("text")]
        if items:
            self.bm25.rebuild(user_id)
            self.bm25.index_bulk(user_id, items)

    def format_memories_for_prompt(self, user_id: str, query: str, k: int = 5) -> str:
        """Return a formatted string of relevant memories for injection into
        the system prompt."""
        memories = self.search(user_id, query, k=k)
        if not memories:
            return ""
        lines = []
        for m in memories:
            lines.append(f"- {m['text']}")
        return "\n".join(lines)
