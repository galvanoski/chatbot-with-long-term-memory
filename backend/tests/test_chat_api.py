from types import SimpleNamespace
from pathlib import Path
import tempfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import routes


class _FakeMiddleware:
    def before_agent(self, state, config):
        return state

    def after_agent(self, state, config):
        return state


class _FakeState:
    def __init__(self, values, next_nodes=None):
        self.values = values
        self.next = tuple(next_nodes or [])


class _FakeGraph:
    def __init__(self):
        self._states: dict[str, dict] = {}

    def invoke(self, initial_state, config):
        thread_id = config["configurable"]["thread_id"]
        state = self._states.setdefault(
            thread_id,
            {
                "messages": [],
                "approval_status": None,
                "draft_copy_de": "",
                "copy_metadata": {},
            },
        )

        if initial_state is None:
            state["approval_status"] = "approved"
            state["messages"].append(
                SimpleNamespace(type="ai", content=state.get("draft_copy_de") or "Publicado correctamente")
            )
            return state

        input_messages = initial_state.get("messages", [])
        prompt = input_messages[-1].content if input_messages else ""

        for msg in input_messages:
            state["messages"].append(msg)

        state["draft_copy_de"] = f"Borrador para: {prompt}"
        state["copy_metadata"] = {"hashtags": ["#geekcat", "#marketing"]}
        state["approval_status"] = "pending"
        state["messages"].append(
            SimpleNamespace(type="ai", content=state["draft_copy_de"])
        )
        return state

    def get_state(self, config):
        thread_id = config["configurable"]["thread_id"]
        state = self._states.get(
            thread_id,
            {
                "messages": [],
                "approval_status": None,
                "draft_copy_de": "",
                "copy_metadata": {},
            },
        )
        next_nodes = (
            ["publisher"]
            if state.get("draft_copy_de") and state.get("approval_status") != "approved"
            else []
        )
        return _FakeState(state, next_nodes)

    def update_state(self, config, updates):
        thread_id = config["configurable"]["thread_id"]
        state = self._states.setdefault(
            thread_id,
            {
                "messages": [],
                "approval_status": None,
                "draft_copy_de": "",
                "copy_metadata": {},
            },
        )
        state.update(updates)


class _FakeMemory:
    def __init__(self):
        self.events: list[dict] = []

    def save_analytics(self, user_id: str, event_type: str, payload: dict | None = None):
        self.events.append({
            "user_id": user_id,
            "event_type": event_type,
            "payload": payload or {}
        })


def _build_test_client() -> TestClient:
    routes._threads.clear()
    temp_db = tempfile.NamedTemporaryFile(suffix='-threads.db', delete=False)
    temp_db.close()
    routes._set_thread_db_path(Path(temp_db.name))
    routes._graph = _FakeGraph()
    routes._middleware = _FakeMiddleware()
    routes._memory = _FakeMemory()

    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def test_extract_messages_maps_roles_and_content_unit():
    values = {
        "messages": [
            SimpleNamespace(type="human", content="Hola"),
            SimpleNamespace(type="ai", content="Respuesta"),
        ]
    }

    result = routes._extract_messages(values)

    assert len(result) == 2
    assert result[0]["role"] == "user"
    assert result[0]["content"] == "Hola"
    assert result[1]["role"] == "assistant"
    assert result[1]["content"] == "Respuesta"


def test_derive_thread_title_from_messages_summarizes_progression_unit():
    messages = [
        {"role": "user", "content": "Necesito copy para camiseta Bitcoin"},
        {"role": "assistant", "content": "Borrador..."},
        {"role": "user", "content": "ajusta el tono para LinkedIn"},
    ]

    title = routes._derive_thread_title_from_messages(messages)
    lower_title = title.lower()

    assert "copy para camiseta bitcoin" in lower_title
    assert "ajusta el tono para linkedin" in lower_title
    assert "|" in title


def test_chat_thread_lifecycle_integration():
    client = _build_test_client()

    create = client.post("/api/chat/threads", json={"user_id": "u_test"})
    assert create.status_code == 200
    thread = create.json()
    thread_id = thread["id"]

    send = client.post(
        f"/api/chat/threads/{thread_id}/messages",
        json={"user_id": "u_test", "content": "Necesito copy para camiseta Bitcoin"},
    )
    assert send.status_code == 200
    payload = send.json()
    assert payload["status"] == "awaiting_approval"
    assert payload["pending_copy"]["content"].startswith("Borrador para:")
    assert len(payload["messages"]) >= 2

    fetch = client.get(f"/api/chat/threads/{thread_id}", params={"user_id": "u_test"})
    assert fetch.status_code == 200
    fetched_thread = fetch.json()
    assert fetched_thread["id"] == thread_id
    assert len(fetched_thread["messages"]) >= 2
    assert any(m["role"] == "assistant" for m in fetched_thread["messages"])
    assert fetched_thread["status"] == "awaiting_approval"
    assert fetched_thread["pending_copy"]["content"].startswith("Borrador para:")
    assert fetched_thread["pending_copy"]["hashtags"] == ["#geekcat", "#marketing"]


def test_approve_with_edited_parts_integration():
    client = _build_test_client()

    create = client.post("/api/chat/threads", json={"user_id": "u_test"})
    thread_id = create.json()["id"]

    client.post(
        f"/api/chat/threads/{thread_id}/messages",
        json={"user_id": "u_test", "content": "Genera copy para hoodie"},
    )

    approve = client.post(
        f"/api/chat/threads/{thread_id}/approve",
        json={
            "user_id": "u_test",
            "edited_parts": {
                "hook": "Debug first, panic later.",
                "body": "Dieser Hoodie ist stabiler als dein letzter Deploy.",
                "cta": "Hol ihn dir, bevor die Logs dich holen."
            },
            "edited_copy": "Debug first, panic later.\n\nDieser Hoodie ist stabiler als dein letzter Deploy.\n\nHol ihn dir, bevor die Logs dich holen."
        },
    )
    assert approve.status_code == 200
    payload = approve.json()
    assert payload["status"] == "published"
    assert any("Debug first" in m["content"] for m in payload["messages"])
    events = routes._memory.events
    assert any(e["event_type"] == "human_feedback" and e["payload"].get("rating") == "up" for e in events)


def test_catalog_bulk_load_endpoint_integration(monkeypatch):
    client = _build_test_client()

    monkeypatch.setattr(routes, "load_products_to_catalog", lambda items: len(items))

    res = client.post(
        "/api/catalog/products/bulk",
        json={
            "items": [
                {
                    "id": "sku-1",
                    "text": "Produkt mit sarkastischer Legende",
                    "metadata": {
                        "sku": "SKU-1",
                        "name": "HODL TIGHT Hoodie",
                        "sarcastic_legend": "Stable hoodie, unstable market."
                    }
                }
            ]
        },
    )
    assert res.status_code == 200
    assert res.json()["loaded"] == 1


def test_thread_survives_cache_reset_integration():
    client = _build_test_client()

    create = client.post("/api/chat/threads", json={"user_id": "u_test"})
    thread_id = create.json()["id"]

    send = client.post(
        f"/api/chat/threads/{thread_id}/messages",
        json={"user_id": "u_test", "content": "Necesito copy persistente"},
    )
    assert send.status_code == 200

    routes._threads.clear()

    fetch = client.get(f"/api/chat/threads/{thread_id}", params={"user_id": "u_test"})
    assert fetch.status_code == 200
    payload = fetch.json()
    assert payload["id"] == thread_id
    assert payload["status"] == "awaiting_approval"
    assert any(m["role"] == "assistant" for m in payload["messages"])
    assert payload["pending_copy"]["content"].startswith("Borrador para:")


def test_reject_saves_human_feedback_event():
    client = _build_test_client()

    create = client.post("/api/chat/threads", json={"user_id": "u_test"})
    thread_id = create.json()["id"]

    client.post(
        f"/api/chat/threads/{thread_id}/messages",
        json={"user_id": "u_test", "content": "Genera copy para camiseta"},
    )

    reject = client.post(
        f"/api/chat/threads/{thread_id}/reject",
        json={"user_id": "u_test", "feedback": "too generic"},
    )
    assert reject.status_code == 200
    events = routes._memory.events
    assert any(
        e["event_type"] == "human_feedback"
        and e["payload"].get("rating") == "down"
        and e["payload"].get("feedback") == "too generic"
        for e in events
    )


def test_approve_flow_updates_status_integration():
    client = _build_test_client()

    create = client.post("/api/chat/threads", json={"user_id": "u_test"})
    thread_id = create.json()["id"]

    client.post(
        f"/api/chat/threads/{thread_id}/messages",
        json={"user_id": "u_test", "content": "Genera copy para hoodie"},
    )

    approve = client.post(
        f"/api/chat/threads/{thread_id}/approve",
        json={"user_id": "u_test"},
    )
    assert approve.status_code == 200
    approved_payload = approve.json()
    assert approved_payload["status"] == "published"
    assert len(approved_payload["messages"]) >= 1