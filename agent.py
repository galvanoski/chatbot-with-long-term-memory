import os
import sqlite3

from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite import SqliteSaver

from memory_db import save_memory, search_memories


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
        save_memory(user_id, fact)
        return f"Saved to long-term memory: {fact}"

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