"""FastAPI dependency injection for shared instances."""

from functools import lru_cache

from backend.memory.manager import MemoryManager
from backend.memory.long_term import LongTermMemory
from backend.memory.compaction import CompactionEngine
from backend.memory.search.hybrid import HybridSearchEngine
from backend.memory.search.sqlite_fts5 import SQLiteFTS5
from backend.middleware.geekcat import GeekCatMiddleware
from backend.rag.vectorstore import ChromaStore


@lru_cache
def get_chroma_store() -> ChromaStore:
    return ChromaStore()


@lru_cache
def get_bm25() -> SQLiteFTS5:
    return SQLiteFTS5()


@lru_cache
def get_long_term_memory() -> LongTermMemory:
    return LongTermMemory(
        vector_store=get_chroma_store(),
        bm25=get_bm25(),
    )


@lru_cache
def get_compaction_engine() -> CompactionEngine:
    return CompactionEngine()


@lru_cache
def get_hybrid_search() -> HybridSearchEngine:
    return HybridSearchEngine(
        vector_store=get_chroma_store(),
        bm25=get_bm25(),
        rrf_constant=60,
    )


@lru_cache
def get_memory_manager() -> MemoryManager:
    return MemoryManager(
        ltm=get_long_term_memory(),
        compaction=get_compaction_engine(),
        hybrid_search=get_hybrid_search(),
    )


@lru_cache
def get_middleware() -> GeekCatMiddleware:
    return GeekCatMiddleware(memory=get_memory_manager())
