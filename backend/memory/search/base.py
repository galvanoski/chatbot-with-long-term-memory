from abc import ABC, abstractmethod
from typing import Any


class BM25Engine(ABC):
    """Abstract BM25 keyword search engine.

    Implementations: SQLiteFTS5, ElasticsearchBM25
    """

    @abstractmethod
    def index(self, namespace: str, doc_id: str, text: str) -> None:
        """Index a document for keyword search."""
        ...

    @abstractmethod
    def index_bulk(self, namespace: str, items: list[tuple[str, str]]) -> int:
        """Index multiple documents at once. Returns count indexed."""
        ...

    @abstractmethod
    def search(
        self, namespace: str, query: str, k: int = 10
    ) -> list[tuple[str, float, str]]:
        """Search for documents matching the query.

        Returns list of (doc_id, score, text) sorted by relevance descending.
        """
        ...

    @abstractmethod
    def delete(self, namespace: str, doc_id: str) -> None:
        """Remove a document from the index."""
        ...

    @abstractmethod
    def rebuild(self, namespace: str) -> None:
        """Rebuild the index for a namespace (e.g. after bulk changes)."""
        ...
