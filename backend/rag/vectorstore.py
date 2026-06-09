import os
import re
import uuid
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings

from backend.rag.embeddings import get_embeddings


class ChromaStore:
    """Persistent Chroma vector store with per-user collections.

    Collection naming:
      - user_{user_id}  → personal memories, brand rules, preferences
      - pod_catalog     → global POD product catalog (shared across users)
      - meme_repo       → global meme / technical concept repository
    """

    def __init__(self, persist_dir: str | None = None):
        if persist_dir is None:
            persist_dir = str(Path(__file__).resolve().parents[1] / "chroma_db")
        self._persist_dir = persist_dir
        self._client = chromadb.PersistentClient(
            path=persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )
        self._embedding_function = get_embeddings()

    # ── Collection helpers ──

    def _collection_name(self, user_id: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_]", "_", user_id)
        return f"user_{safe}"

    def _get_collection(self, name: str):
        return self._client.get_or_create_collection(
            name=name,
            embedding_function=self._embedding_function,
        )

    def get_user_collection(self, user_id: str):
        return self._get_collection(self._collection_name(user_id))

    def get_global_collection(self, name: str):
        return self._get_collection(name)

    # ── CRUD operations ──

    def add_memory(
        self,
        user_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Store a text memory for a user. Returns the document id."""
        doc_id = str(uuid.uuid4())
        coll = self.get_user_collection(user_id)
        coll.add(
            ids=[doc_id],
            documents=[text],
            metadatas=[metadata or {}],
        )
        return doc_id

    def search_user_memories(
        self,
        user_id: str,
        query: str,
        k: int = 5,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Semantic search over a user's personal collection."""
        coll = self.get_user_collection(user_id)
        count = coll.count()
        if count == 0:
            return []
        n_results = min(k, count)
        results = coll.query(
            query_texts=[query],
            n_results=n_results,
            where=where,
        )
        return self._format_results(results)

    def search_global(
        self,
        collection_name: str,
        query: str,
        k: int = 5,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Semantic search over a global collection (e.g. pod_catalog)."""
        coll = self.get_global_collection(collection_name)
        count = coll.count()
        if count == 0:
            return []
        n_results = min(k, count)
        results = coll.query(
            query_texts=[query],
            n_results=n_results,
            where=where,
        )
        return self._format_results(results)

    def delete_memory(self, user_id: str, doc_id: str) -> None:
        coll = self.get_user_collection(user_id)
        coll.delete(ids=[doc_id])

    def delete_memories_by_metadata(self, user_id: str, where: dict[str, Any]) -> None:
        coll = self.get_user_collection(user_id)
        coll.delete(where=where)

    def list_memories(
        self,
        user_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List all memories for a user (latest first)."""
        coll = self.get_user_collection(user_id)
        count = coll.count()
        if count == 0:
            return []
        results = coll.get(limit=min(limit, count))
        return [
            {"id": results["ids"][i], "text": results["documents"][i]}
            for i in range(len(results["ids"]))
        ]

    def count_memories(self, user_id: str) -> int:
        coll = self.get_user_collection(user_id)
        return coll.count()

    # ── Bulk operations ──

    def add_global_items(
        self,
        collection_name: str,
        items: list[dict[str, Any]],
    ) -> int:
        """Add multiple items to a global collection. Each item must have
        at least 'text', optionally 'id' and 'metadata'."""
        coll = self.get_global_collection(collection_name)
        ids = []
        documents = []
        metadatas = []
        for item in items:
            ids.append(item.get("id", str(uuid.uuid4())))
            documents.append(item["text"])
            metadatas.append(item.get("metadata", {}))
        coll.add(ids=ids, documents=documents, metadatas=metadatas)
        return len(ids)

    # ── Internal ──

    def _format_results(self, results) -> list[dict[str, Any]]:
        formatted = []
        if not results or not results["ids"]:
            return formatted
        for i in range(len(results["ids"][0])):
            formatted.append({
                "id": results["ids"][0][i],
                "text": results["documents"][0][i] if results.get("documents") else "",
                "score": results["distances"][0][i] if results.get("distances") else 0.0,
                "metadata": results["metadatas"][0][i] if results.get("metadatas") else {},
            })
        return formatted
