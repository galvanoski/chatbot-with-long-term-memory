import os
import uuid

from langchain_openai import OpenAIEmbeddings
from langgraph.store.memory import InMemoryStore

_embeddings = OpenAIEmbeddings(
    model="text-embedding-3-small",
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ["OPENROUTER_API_KEY"],
)

_store = InMemoryStore(index={"embed": _embeddings, "dims": 1536})


def init_memory_db() -> None:
    pass


def save_memory(user_id: str, fact: str) -> None:
    _store.put(
        ("memories", user_id),
        str(uuid.uuid4()),
        {"text": fact},
    )


def get_memories(user_id: str) -> list[str]:
    items = _store.search(("memories", user_id))
    return [item.value["text"] for item in items]


def search_memories(user_id: str, query: str, limit: int = 5) -> list[str]:
    items = _store.search(("memories", user_id), query=query, limit=limit)
    return [item.value["text"] for item in items]
