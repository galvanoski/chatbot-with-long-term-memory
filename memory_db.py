import os
import sqlite3
import uuid
from pathlib import Path

from langchain_openai import OpenAIEmbeddings
from langgraph.store.memory import InMemoryStore

_embeddings = OpenAIEmbeddings(
    model="text-embedding-3-small",
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ["OPENROUTER_API_KEY"],
)

_store = InMemoryStore(index={"embed": _embeddings, "dims": 1536})


def _migrate_old_sqlite():
    flag = "_migrated.flag"
    if os.path.exists(flag):
        return
    try:
        old = sqlite3.connect("memories.db")
        rows = old.execute("SELECT user_id, fact FROM memories").fetchall()
        old.close()
    except Exception:
        return
    for user_id, fact in rows:
        _store.put(
            ("memories", user_id),
            str(uuid.uuid4()),
            {"text": fact},
        )
    Path(flag).touch()


_migrate_old_sqlite()


def init_memory_db() -> None:
    pass


def save_memory(user_id: str, fact: str) -> None:
    _store.put(
        ("memories", user_id),
        str(uuid.uuid4()),
        {"text": fact},
    )


def get_memories(user_id: str) -> list[tuple[str, str]]:
    items = _store.search(("memories", user_id), limit=1000)
    return [(item.key, item.value["text"]) for item in items]


def search_memories(user_id: str, query: str, limit: int = 5) -> list[str]:
    items = _store.search(("memories", user_id), query=query, limit=limit)
    return [item.value["text"] for item in items]


def delete_memory(user_id: str, memory_id: str) -> None:
    _store.delete(("memories", user_id), memory_id)
