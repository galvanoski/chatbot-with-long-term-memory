import sqlite3
import threading
from typing import Any

from backend.memory.search.base import BM25Engine


class SQLiteFTS5(BM25Engine):
    """BM25 keyword search using SQLite FTS5 virtual tables.

    Each namespace (e.g. user_id) gets its own FTS virtual table
    for isolation. Table names are sanitised.
    """

    def __init__(self, db_path: str = "backend/fts5_index.db"):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            # Enable WAL mode for concurrent reads
            conn.execute("PRAGMA journal_mode=WAL")
            conn.commit()
            conn.close()

    def _table_name(self, namespace: str) -> str:
        safe = "".join(c for c in namespace if c.isalnum() or c == "_")
        return f"fts_{safe}"

    def _ensure_table(self, namespace: str):
        table = self._table_name(namespace)
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                f"""CREATE VIRTUAL TABLE IF NOT EXISTS {table}
                    USING fts5(doc_id UNINDEXED, text, tokenize='unicode61')"""
            )
            conn.commit()
        finally:
            conn.close()

    def index(self, namespace: str, doc_id: str, text: str) -> None:
        self._ensure_table(namespace)
        table = self._table_name(namespace)
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.execute(
                    f"INSERT OR REPLACE INTO {table}(doc_id, text) VALUES (?, ?)",
                    (doc_id, text),
                )
                conn.commit()
            finally:
                conn.close()

    def index_bulk(self, namespace: str, items: list[tuple[str, str]]) -> int:
        self._ensure_table(namespace)
        table = self._table_name(namespace)
        count = 0
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.execute("BEGIN")
                for doc_id, text in items:
                    conn.execute(
                        f"INSERT OR REPLACE INTO {table}(doc_id, text) VALUES (?, ?)",
                        (doc_id, text),
                    )
                    count += 1
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        return count

    def search(
        self, namespace: str, query: str, k: int = 10
    ) -> list[tuple[str, float, str]]:
        self._ensure_table(namespace)
        table = self._table_name(namespace)
        conn = sqlite3.connect(self._db_path)
        try:
            # FTS5 MATCH syntax: wrap each word as a prefix query for partial matches
            fts_query = " AND ".join(f'"{word}"*' for word in query.split() if word)
            if not fts_query:
                return []
            cursor = conn.execute(
                f"""SELECT doc_id, rank, text
                    FROM {table}
                    WHERE text MATCH ?
                    ORDER BY rank
                    LIMIT ?""",
                (fts_query, k),
            )
            results = []
            for doc_id, rank, text in cursor.fetchall():
                # SQLite FTS5 rank: lower = better. Convert to a positive score.
                score = 1.0 / (1.0 + abs(rank))
                results.append((doc_id, score, text))
            return results
        finally:
            conn.close()

    def delete(self, namespace: str, doc_id: str) -> None:
        table = self._table_name(namespace)
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.execute(
                    f"DELETE FROM {table} WHERE doc_id = ?",
                    (doc_id,),
                )
                conn.commit()
            finally:
                conn.close()

    def rebuild(self, namespace: str) -> None:
        """Rebuild the FTS index (optimize)."""
        table = self._table_name(namespace)
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.execute(f"INSERT INTO {table}({table}) VALUES('rebuild')")
                conn.commit()
            finally:
                conn.close()
