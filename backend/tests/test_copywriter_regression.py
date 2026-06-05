import json

from langchain_core.messages import AIMessage, HumanMessage

from backend.graph.nodes import copywriter


class _FakeResponse:
    def __init__(self, content: str):
        self.content = content
        self._validation = {}


class _FakeChatOpenAI:
    responses: list[str] = []

    def __init__(self, *args, **kwargs):
        self._cursor = 0

    def invoke(self, _messages):
        if self._cursor >= len(self.responses):
            raise AssertionError("Fake LLM ran out of queued responses")
        content = self.responses[self._cursor]
        self._cursor += 1
        return _FakeResponse(content)


def test_copywriter_preserves_hashtags_on_style_revision(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(copywriter, "ChatOpenAI", _FakeChatOpenAI)

    # 1) Planner overreaches and asks for broad changes.
    planner_payload = {
        "mode": "revise",
        "explicit_new_post_request": False,
        "reason": "style tweak",
        "edit_scope": "make it funnier",
        "fields_to_update": ["hook", "body", "cta", "hashtags", "full_post"],
    }

    # 2) Guardrails explicitly preserve hashtags.
    guard_payload = {
        "explicit_new_post_request": False,
        "change_fields": ["hook", "body", "cta"],
        "preserve_fields": ["hashtags"],
        "notes": "No explicit hashtag request",
    }

    # 3) Generator tries to change hashtags, but merge logic must keep previous ones.
    generation_payload = {
        "hook": "Jetzt mit mehr Humor.",
        "body": "Der Build failed, aber die Katze approved den Merge.",
        "cta": "Deploy den Look.",
        "hashtags": ["#neu", "#fresh"],
        "style_notes": {"sarcasm_level": "8", "used_product_legend": True},
    }

    _FakeChatOpenAI.responses = [
        json.dumps(planner_payload),
        json.dumps(guard_payload),
        json.dumps(generation_payload),
    ]

    previous_hashtags = ["#old1", "#old2", "#old3"]
    state = {
        "messages": [
            AIMessage(content="Alt text"),
            HumanMessage(content="Hazlo mas gracioso"),
        ],
        "copy_metadata": {
            "hashtags": previous_hashtags,
            "parts": {
                "hook": "Alter Hook",
                "body": "Alter Body",
                "cta": "Alte CTA",
            },
        },
        "product_context": [],
        "trend_insights": "",
        "meme_references": [],
        "brand_rules": {},
        "human_feedback": "",
    }

    result = copywriter.copywriter_node(state)

    assert result["copy_metadata"]["hashtags"] == previous_hashtags
    assert "#old1 #old2 #old3" in result["draft_copy_de"]


def test_copywriter_allows_full_replacement_on_explicit_new_post(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(copywriter, "ChatOpenAI", _FakeChatOpenAI)

    planner_payload = {
        "mode": "new",
        "explicit_new_post_request": True,
        "reason": "user asked new variant",
        "edit_scope": "full replacement",
        "fields_to_update": ["full_post"],
    }
    guard_payload = {
        "explicit_new_post_request": True,
        "change_fields": ["full_post"],
        "preserve_fields": [],
        "notes": "Explicitly new post",
    }
    generation_payload = {
        "hook": "Neuer Hook",
        "body": "Neuer Body",
        "cta": "Neue CTA",
        "hashtags": ["#new1", "#new2"],
        "style_notes": {"sarcasm_level": "7", "used_product_legend": False},
    }

    _FakeChatOpenAI.responses = [
        json.dumps(planner_payload),
        json.dumps(guard_payload),
        json.dumps(generation_payload),
    ]

    state = {
        "messages": [
            AIMessage(content="Post anterior"),
            HumanMessage(content="Crea un post totalmente nuevo"),
        ],
        "copy_metadata": {
            "hashtags": ["#old"],
            "parts": {"hook": "Old", "body": "Old", "cta": "Old"},
        },
        "product_context": [],
        "trend_insights": "",
        "meme_references": [],
        "brand_rules": {},
        "human_feedback": "",
    }

    result = copywriter.copywriter_node(state)

    assert result["copy_metadata"]["hashtags"] == ["#new1", "#new2"]
    assert "#new1 #new2" in result["draft_copy_de"]
