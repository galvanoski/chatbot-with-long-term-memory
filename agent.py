import os
import sqlite3

import msgpack
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite import SqliteSaver

from memory_db import search_memories

# Thread-safe pending memory staging — tools run in a thread pool executor
# so they cannot write to st.session_state directly.
_pending_memory: tuple[str, str] | None = None


def _build_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model="openai/gpt-5-mini",
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],  # loaded by app.py via dotenv
    )

# One shared connection for the checkpointer, reused across requests
_THREADS_CONN = sqlite3.connect("threads.db", check_same_thread=False)
_CHECKPOINTER = SqliteSaver(_THREADS_CONN)
_CHECKPOINTER.setup()


def _make_remember_tool(user_id: str):
    """Factory that binds the current user_id into the tool via a closure."""

    @tool
    def remember(fact: str) -> str:
        """Save a personal fact about the user to long-term memory.

        Call this whenever the user tells you something worth keeping
        between conversations (food preferences, location, goals, name,
        allergies, and so on).
        """
        global _pending_memory
        _pending_memory = (user_id, fact)
        return f"Pending your confirmation to save: {fact}"

    return remember


def build_agent(user_id: str, query: str = ""):
    """Return a fresh agent with the current user's memories loaded into its prompt."""
    memories = search_memories(user_id, query=query, limit=5)
    memory_bullets = "\n".join(f"- {m}" for m in memories) or "(nothing yet)"

    system_prompt = (
        "You are a helpful assistant with long-term memory.\n\n"
        f"What you remember about this user:\n{memory_bullets}\n\n"
        "IMPORTANT: You have no persistent memory of your own — your context "
        "window is erased between sessions. Whenever the user tells you "
        "anything personal (preferences, dietary needs, location, goals, name, "
        "allergies, or any fact they want recalled later), you MUST call the "
        "`remember` tool immediately. Do not say 'I'll remember that' without "
        "calling the tool; saying it does not save anything."
    )

    return create_agent(
        model=_build_llm(),
        tools=[_make_remember_tool(user_id)],
        system_prompt=system_prompt,
        checkpointer=_CHECKPOINTER,
    )


def _decode_messages(thread_id: str) -> list[dict]:
    conn = sqlite3.connect("threads.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT checkpoint FROM checkpoints WHERE thread_id = ? ORDER BY checkpoint_id DESC LIMIT 1",
        (thread_id,),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return []
    cp = msgpack.unpackb(row[0], raw=False)
    cv = cp.get("channel_values", {})
    # Messages can be in channel_values.messages or channel_values.__start__.messages
    msgs = cv.get("messages") or cv.get("__start__", {}).get("messages") or []
    result = []
    for m in msgs:
        if isinstance(m, dict):
            result.append({"role": m.get("role", "?"), "content": m.get("content", "")})
        elif isinstance(m, msgpack.ext.ExtType):
            inner = msgpack.unpackb(m.data)
            data = inner[2]
            role = data.get("type", "?")
            content = data.get("content", "")
            if role == "tool":
                continue
            result.append({"role": role, "content": content})
    return result


def pop_pending_memory() -> tuple[str, str] | None:
    global _pending_memory
    item = _pending_memory
    _pending_memory = None
    return item


def summarise_thread(thread_id: str) -> str:
    messages = _decode_messages(thread_id)
    if not messages:
        return "Empty chat"
    chat_text = "\n".join(
        f"{m['role']}: {m['content']}" for m in messages if m["content"]
    )[:1500]
    if not chat_text.strip():
        return "Empty chat"
    llm = _build_llm()
    response = llm.invoke(
        [
            ("system", "Return ONLY a one-line label (max 8 words) describing this conversation. No prefix, no explanation."),
            ("human", chat_text),
        ]
    )
    return response.content.strip()