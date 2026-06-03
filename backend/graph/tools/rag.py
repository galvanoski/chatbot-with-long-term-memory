"""RAG tools for querying the POD product catalog and meme repository."""

from backend.rag.vectorstore import ChromaStore

POD_COLLECTION = "pod_catalog"
MEME_COLLECTION = "meme_repo"


_product_store: ChromaStore | None = None


def _get_store() -> ChromaStore:
    global _product_store
    if _product_store is None:
        from backend.rag.vectorstore import ChromaStore
        _product_store = ChromaStore()
    return _product_store


def query_product_catalog(query: str, top_k: int = 3) -> list[dict]:
    """Search the POD product catalog for products matching the query.

    Returns list of dicts with keys: id, text, score, metadata.
    """
    store = _get_store()
    return store.search_global(POD_COLLECTION, query, k=top_k)


def query_meme_repository(query: str, top_k: int = 2) -> list[dict]:
    """Search the meme / technical concept repository."""
    store = _get_store()
    return store.search_global(MEME_COLLECTION, query, k=top_k)


def load_products_to_catalog(items: list[dict]) -> int:
    """Bulk-load product items into the global POD catalog collection.

    Each item must have at minimum:
      - "text": product description / title
      - "metadata": dict with "sku", "category", "price", etc.
    """
    store = _get_store()
    return store.add_global_items(POD_COLLECTION, items)


def load_memes_to_repo(items: list[dict]) -> int:
    """Bulk-load meme / concept items into the global meme repository."""
    store = _get_store()
    return store.add_global_items(MEME_COLLECTION, items)
