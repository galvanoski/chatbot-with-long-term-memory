"""Unit and integration tests for the RAG pipeline (query expansion, reranking, compression)."""

import json
import time

import pytest
from langchain_openai import ChatOpenAI

from backend.rag import pipeline as rag_pipeline


# ── Fakes ───────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, content: str):
        self.content = content


class _FakeChatOpenAI:
    """Replaces ChatOpenAI; caller sets responses list before the test."""
    responses: list[str] = []

    def __init__(self, *args, **kwargs):
        self._cursor = 0

    def invoke(self, _messages):
        if self._cursor >= len(self.responses):
            raise AssertionError(
                f"Fake LLM ran out of responses (cursor={self._cursor}, "
                f"queued={len(self.responses)})"
            )
        content = self.responses[self._cursor]
        self._cursor += 1
        # Small sleep so latency_ms > 0 in trace assertions
        time.sleep(0.001)
        return _FakeResponse(content)


# ── Fixtures ────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_fake_llm():
    _FakeChatOpenAI.responses.clear()
    # Ensure each test starts with a clean pipeline LLM singleton
    rag_pipeline._llm = None


@pytest.fixture
def patch_llm(monkeypatch):
    """Replace ChatOpenAI with _FakeChatOpenAI in the pipeline module."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(rag_pipeline, "ChatOpenAI", _FakeChatOpenAI)
    monkeypatch.setattr(rag_pipeline, "_llm", None)


def make_doc(doc_id: str, text: str, score: float = 0.5, **meta) -> dict:
    return {"id": doc_id, "text": text, "score": score, "metadata": meta}


# ═══════════════════════════════════════════════════════
#  _parse_json
# ═══════════════════════════════════════════════════════

class TestParseJson:
    def test_plain_json(self):
        assert rag_pipeline._parse_json('{"a": 1}') == {"a": 1}

    def test_code_block_json(self):
        raw = "```json\n{\"a\": 1}\n```"
        assert rag_pipeline._parse_json(raw) == {"a": 1}

    def test_code_block_no_lang(self):
        raw = "```\n{\"a\": 1}\n```"
        assert rag_pipeline._parse_json(raw) == {"a": 1}

    def test_embedded_json(self):
        raw = "Here is the result: {\"a\": 1}\nEnd."
        assert rag_pipeline._parse_json(raw) == {"a": 1}

    def test_invalid_returns_none(self):
        assert rag_pipeline._parse_json("not json") is None

    def test_empty_string(self):
        assert rag_pipeline._parse_json("") is None


# ═══════════════════════════════════════════════════════
#  expand_query
# ═══════════════════════════════════════════════════════

class TestExpandQuery:
    def test_returns_original_when_llm_fails(self, patch_llm):
        """Graceful degradation: returns [query] if LLM throws."""
        _FakeChatOpenAI.responses = []  # no responses -> fail
        result = rag_pipeline.expand_query("cat t-shirt")
        assert result == ["cat t-shirt"]

    def test_returns_variants(self, patch_llm):
        _FakeChatOpenAI.responses = [
            json.dumps({"queries": ["cat tee", "feline apparel", "cat clothing"]})
        ]
        result = rag_pipeline.expand_query("cat t-shirt")
        assert result[0] == "cat t-shirt"
        assert "cat tee" in result
        assert "feline apparel" in result
        assert "cat clothing" in result

    def test_short_query_skips_llm(self, patch_llm):
        """Queries under 3 chars are returned as-is without calling the LLM."""
        _FakeChatOpenAI.responses = [json.dumps({"queries": ["x"]})]
        result = rag_pipeline.expand_query("ab")
        assert result == ["ab"]
        # no LLM called — cursor stays at 0

    def test_respects_n_variants(self, patch_llm):
        _FakeChatOpenAI.responses = [
            json.dumps({"queries": ["v1", "v2", "v3", "v4"]})
        ]
        result = rag_pipeline.expand_query("query", n_variants=2)
        assert len(result) == 3  # original + 2
        assert "v3" not in result

    def test_llm_returns_invalid_json_fallback(self, patch_llm):
        _FakeChatOpenAI.responses = ["not json"]
        result = rag_pipeline.expand_query("cat t-shirt")
        assert result == ["cat t-shirt"]

    def test_strips_whitespace_from_variants(self, patch_llm):
        _FakeChatOpenAI.responses = [
            json.dumps({"queries": ["  cat tee  ", "\nfeline\n"]})
        ]
        result = rag_pipeline.expand_query("query")
        assert "cat tee" in result
        assert "feline" in result


# ═══════════════════════════════════════════════════════
#  rerank_documents
# ═══════════════════════════════════════════════════════

class TestRerankDocuments:
    def test_short_circuit_empty(self, patch_llm):
        """0 or 1 docs → no LLM call, returns as-is."""
        assert rag_pipeline.rerank_documents("q", []) == []
        doc = [make_doc("1", "text")]
        assert rag_pipeline.rerank_documents("q", doc, top_k=5) == doc

    def test_reranks_and_sets_score(self, patch_llm):
        docs = [
            make_doc("a", "red cat shirt", score=0.5),
            make_doc("b", "blue dog mug", score=0.5),
            make_doc("c", "green cat hat", score=0.5),
        ]
        _FakeChatOpenAI.responses = [
            json.dumps({
                "scores": [
                    {"index": 0, "score": 0.2},
                    {"index": 1, "score": 0.9},
                    {"index": 2, "score": 0.5},
                ]
            })
        ]
        result = rag_pipeline.rerank_documents("cat", docs)
        assert result[0]["id"] == "b"  # highest score
        assert result[1]["id"] == "c"
        assert result[2]["id"] == "a"
        assert result[0]["rerank_score"] == 0.9

    def test_respects_top_k(self, patch_llm):
        docs = [make_doc(str(i), f"doc {i}") for i in range(5)]
        _FakeChatOpenAI.responses = [
            json.dumps({
                "scores": [{"index": i, "score": float(5 - i)} for i in range(5)]
            })
        ]
        result = rag_pipeline.rerank_documents("q", docs, top_k=2)
        assert len(result) == 2

    def test_llm_failure_fallback(self, patch_llm):
        docs = [make_doc("a", "text1"), make_doc("b", "text2")]
        _FakeChatOpenAI.responses = ["invalid"]
        result = rag_pipeline.rerank_documents("q", docs)
        assert len(result) == 2  # returned as-is


# ═══════════════════════════════════════════════════════
#  compress_documents
# ═══════════════════════════════════════════════════════

class TestCompressDocuments:
    def test_empty_docs(self, patch_llm):
        assert rag_pipeline.compress_documents("q", []) == []

    def test_compresses_batch(self, patch_llm):
        docs = [
            make_doc("1", "The red cat shirt is made of 100% cotton. SKU is CAT-001. Price is $29.99."),
            make_doc("2", "The blue dog mug holds 12oz. Dishwasher safe. SKU: DOG-042."),
        ]
        _FakeChatOpenAI.responses = [
            json.dumps({
                "compressed": [
                    {"index": 0, "text": "Red cat shirt. SKU CAT-001. $29.99."},
                    {"index": 1, "text": "Blue dog mug. SKU DOG-042."},
                ]
            })
        ]
        result = rag_pipeline.compress_documents("cat shirt", docs)
        assert result[0].get("compressed_text") == "Red cat shirt. SKU CAT-001. $29.99."
        assert result[1].get("compressed_text") == "Blue dog mug. SKU DOG-042."

    def test_llm_failure_fallback(self, patch_llm):
        docs = [make_doc("1", "Some long text here" * 20)]
        _FakeChatOpenAI.responses = ["bad json"]
        result = rag_pipeline.compress_documents("q", docs)
        assert result[0].get("compressed_text")
        assert len(result[0]["compressed_text"]) <= 500

    def test_compress_document_single(self, patch_llm):
        _FakeChatOpenAI.responses = ["compressed output here"]
        result = rag_pipeline.compress_document("q", "Very long text " * 30)
        assert result == "compressed output here"

    def test_short_text_skipped(self, patch_llm):
        """Text under 100 chars is returned as-is."""
        short = "Hello world"
        result = rag_pipeline.compress_document("q", short)
        assert result == short


# ═══════════════════════════════════════════════════════
#  run_rag_pipeline (orchestration)
# ═══════════════════════════════════════════════════════

class TestRunRagPipeline:
    def test_happy_path(self, patch_llm):
        """Full pipeline: expand → search → rerank → compress → top_k."""
        _FakeChatOpenAI.responses = [
            # 1) Query expansion
            json.dumps({"queries": ["cat tee", "feline shirt", "cat clothing"]}),
            # 2) Re-ranking (3 candidates)
            json.dumps({
                "scores": [
                    {"index": 0, "score": 0.9, "reason": "exact match"},
                    {"index": 1, "score": 0.3, "reason": "irrelevant"},
                    {"index": 2, "score": 0.6, "reason": "partial match"},
                ]
            }),
            # 3) Batch compression (2 docs)
            json.dumps({
                "compressed": [
                    {"index": 0, "text": "Red cat shirt, SKU-001."},
                    {"index": 1, "text": "Feline tee, SKU-002."},
                ]
            }),
        ]

        search_results = {
            "cat tee": [
                make_doc("1", "Red cat shirt, 100% cotton, SKU-001", score=0.8),
                make_doc("2", "Feline tea cozy, SKU-002", score=0.6),
            ],
            "feline shirt": [
                make_doc("3", "Catnip mouse toy, SKU-003", score=0.5),
            ],
        }

        def fake_search(q: str, k: int) -> list[dict]:
            time.sleep(0.001)  # ensure latency > 0
            return search_results.get(q, [])[:k]

        result = rag_pipeline.run_rag_pipeline(
            query="cat shirt",
            search_fn=fake_search,
            top_k=2,
            expand=True,
            rerank=True,
            compress=True,
        )

        assert "results" in result
        assert "queries_used" in result
        assert "trace" in result
        assert len(result["results"]) == 2
        assert len(result["queries_used"]) == 4  # original + 3 variants
        assert len(result["trace"]) == 4  # expand + search_multi + rerank + compress

        # Trace stage names
        stages = [t["stage"] for t in result["trace"]]
        assert stages == ["query_expand", "search_multi", "rerank", "compress"]

        # All traces have latency > 0
        for t in result["trace"]:
            assert t["latency_ms"] > 0

    def test_skip_expand(self, patch_llm):
        _FakeChatOpenAI.responses = [
            json.dumps({
                "scores": [{"index": 0, "score": 1.0}]
            }),
        ]
        result = rag_pipeline.run_rag_pipeline(
            query="cat",
            search_fn=lambda q, k: [make_doc("1", "cat stuff")],
            top_k=1,
            expand=False,
            compress=False,
        )
        stages = [t["stage"] for t in result["trace"]]
        assert stages == ["search_multi", "rerank"]
        assert len(result["queries_used"]) == 1  # no expansion

    def test_skip_rerank(self, patch_llm):
        _FakeChatOpenAI.responses = [
            json.dumps({"compressed": [{"index": 0, "text": "compressed"}]})
        ]
        result = rag_pipeline.run_rag_pipeline(
            query="cat",
            search_fn=lambda q, k: [make_doc("1", "cat stuff")],
            top_k=1,
            expand=False,
            rerank=False,
        )
        stages = [t["stage"] for t in result["trace"]]
        assert stages == ["search_multi", "compress"]

    def test_no_results(self, patch_llm):
        """When search returns nothing, return early."""
        _FakeChatOpenAI.responses = [
            json.dumps({"queries": ["v1"]})
        ]
        result = rag_pipeline.run_rag_pipeline(
            query="zzzzz",
            search_fn=lambda q, k: [],
            top_k=3,
        )
        assert result["results"] == []
        assert len(result["trace"]) == 2  # expand + search_multi

    def test_deduplicates_by_id(self, patch_llm):
        """Same doc returned by different query variants should appear once."""
        _FakeChatOpenAI.responses = [
            json.dumps({"queries": ["v1"]}),
            json.dumps({"scores": [{"index": 0, "score": 1.0}]}),
        ]
        doc = make_doc("same-id", "duplicate text")
        call_count = 0

        def fake_search(q, k):
            nonlocal call_count
            call_count += 1
            return [dict(doc)]  # return a copy each time

        result = rag_pipeline.run_rag_pipeline(
            query="cat",
            search_fn=fake_search,
            top_k=1,
            expand=True,
            rerank=True,
            compress=False,
        )
        assert len(result["results"]) == 1  # deduplicated

    def test_pipeline_total_latency_tracked(self, patch_llm):
        _FakeChatOpenAI.responses = [
            json.dumps({"queries": ["v1"]}),
            json.dumps({"scores": [{"index": 0, "score": 0.5}]}),
        ]
        result = rag_pipeline.run_rag_pipeline(
            query="cat",
            search_fn=lambda q, k: [make_doc("1", "stuff")],
            top_k=1,
            compress=False,
        )
        total = sum(t["latency_ms"] for t in result["trace"])
        assert total > 0


# ═══════════════════════════════════════════════════════
#  Integration: graph/tools/rag.py
# ═══════════════════════════════════════════════════════

class TestRagTools:
    def test_query_product_catalog_integration(self, patch_llm, monkeypatch):
        """Verify the tool calls the pipeline and returns processed results."""
        _FakeChatOpenAI.responses = [
            json.dumps({"queries": ["cat tee"]}),
            json.dumps({"scores": [{"index": 0, "score": 0.8}]}),
        ]

        import backend.graph.tools.rag as rag_tools
        from unittest.mock import MagicMock

        fake_store = MagicMock()
        fake_store.search_global.return_value = [
            {"id": "sku-1", "text": "Cool cat shirt", "score": 0.5,
             "metadata": {"sku": "CAT-001", "name": "Cat Shirt", "category": "t-shirt"}}
        ]
        monkeypatch.setattr(rag_tools, "_get_store", lambda: fake_store)
        rag_tools._product_store = None

        results = rag_tools.query_product_catalog("cat shirt", top_k=3)
        assert len(results) >= 1

    def test_query_meme_repository_integration(self, patch_llm, monkeypatch):
        """Verify the meme tool returns results after pipeline processing."""
        _FakeChatOpenAI.responses = [
            json.dumps({"queries": ["feline joke"]}),
            json.dumps({"scores": [{"index": 0, "score": 0.9}]}),
        ]

        import backend.graph.tools.rag as rag_tools
        from unittest.mock import MagicMock

        fake_store = MagicMock()
        fake_store.search_global.return_value = [
            {"id": "mem-1", "text": "Cats in hats meme", "score": 0.7,
             "metadata": {"source": "memes"}}
        ]
        monkeypatch.setattr(rag_tools, "_get_store", lambda: fake_store)
        rag_tools._product_store = None

        results = rag_tools.query_meme_repository("cat memes", top_k=2)
        assert len(results) >= 1


# ═══════════════════════════════════════════════════════
#  Integration: memory/manager.py retrieve
# ═══════════════════════════════════════════════════════

class TestMemoryManagerRetrieve:
    def test_retrieve_runs_pipeline(self, patch_llm, monkeypatch):
        """MemoryManager.retrieve should expand, search, rerank, compress."""
        _FakeChatOpenAI.responses = [
            json.dumps({"queries": ["memory query v1"]}),
            json.dumps({"scores": [{"index": 0, "score": 0.8}]}),
            json.dumps({"compressed": [{"index": 0, "text": "Key fact from memory."}]}),
        ]

        from backend.memory.manager import MemoryManager
        from unittest.mock import MagicMock

        mock_hybrid = MagicMock()
        mock_hybrid.hybrid_search.return_value = [
            {"id": "mem-1", "text": "User likes cats. User hates dogs. User bought red shirt.",
             "score": 0.9, "metadata": {}},
            {"id": "mem-2", "text": "User mentioned loving cat memes and Python jokes.",
             "score": 0.7, "metadata": {}},
        ]

        mgr = MemoryManager(
            ltm=MagicMock(),
            compaction=MagicMock(),
            hybrid_search=mock_hybrid,
        )

        results = mgr.retrieve("user-1", "cat preferences", k=1)
        assert len(results) >= 1
        # text should be the compressed version
        assert "Key fact" in results[0].get("text", "")
