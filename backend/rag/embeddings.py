import os
from typing import Any, Sequence

from langchain_openai import OpenAIEmbeddings


_embeddings: "ChromaEmbeddingFunction | None" = None


class ChromaEmbeddingFunction:
    """Wraps LangChain's OpenAIEmbeddings so ChromaDB 1.5.x can use it.

    Chroma 1.5.x expects:
      - __call__(self, input: list[str]) -> list[list[float]]   — for documents
      - embed_query(self, input: list[str]) -> list[list[float]] — for queries
      - name(self) -> str
    """

    def __init__(self, model: str = "text-embedding-3-small"):
        self._model = model
        self._wrapped = OpenAIEmbeddings(
            model=model,
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
        )

    def name(self) -> str:
        return self._model

    def __call__(self, input: list[str]) -> list[list[float]]:
        return self._wrapped.embed_documents(input)

    def embed_query(self, input: list[str]) -> list[list[float]]:
        return self._wrapped.embed_documents(input)


def get_embeddings() -> ChromaEmbeddingFunction:
    global _embeddings
    if _embeddings is None:
        _embeddings = ChromaEmbeddingFunction()
    return _embeddings
