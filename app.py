from uuid import uuid4

from dotenv import load_dotenv
import streamlit as st

load_dotenv()

from agent import build_agent, pop_pending_memory, summarise_thread
from memory_db import get_memories, save_memory, delete_memory

st.set_page_config(page_title="Memory Chat", page_icon="🧠")

# --- Sidebar: user, thread control, and memory viewer ---
with st.sidebar:
    st.header("Session")
    user_id = st.text_input("User ID", value="alice")

    # Start a fresh thread on first run or whenever the user ID changes,
    # so switching users does not inherit another user's thread history.
    if st.session_state.get("user_id") != user_id:
        st.session_state.user_id = user_id
        st.session_state.thread_id = str(uuid4())
        st.session_state.messages = []

    if st.button("Start a new chat"):
        st.session_state.thread_id = str(uuid4())
        st.session_state.messages = []
        st.session_state.pop("pending_memory", None)
        st.rerun()

    st.caption(f"Current thread: `{st.session_state.thread_id[:8]}...`")

    st.header("Threads")
    import sqlite3
    _conn = sqlite3.connect("threads.db")
    rows = _conn.execute("SELECT DISTINCT thread_id FROM checkpoints ORDER BY thread_id").fetchall()
    _conn.close()
    for (tid,) in rows:
        label = st.session_state.get(f"label_{tid}")
        if label is None:
            label = summarise_thread(tid)
            st.session_state[f"label_{tid}"] = label
        active = " ►" if tid == st.session_state.thread_id else ""
        st.caption(f"{label}{active}")

    st.header("Long-term memory")
    memories = get_memories(user_id)
    if memories:
        for mem_id, fact in memories:
            col1, col2 = st.columns([5, 1])
            with col1:
                st.write(f"- {fact}")
            with col2:
                if st.button("✕", key=f"del_{mem_id}", help="Delete this memory"):
                    delete_memory(user_id, mem_id)
                    st.rerun()
    else:
        st.write("_No memories yet._")

# --- Main pane: chat ---
st.title("🧠 Memory Chat")
st.caption(
    "Tell the bot to remember something, click 'Start a new chat', "
    "and watch it recall the fact in the next conversation."
)

for msg in st.session_state.get("messages", []):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# --- Pending memory confirmation ---
if "pending_memory" in st.session_state:
    pending_user, pending_fact = st.session_state.pending_memory
    with st.chat_message("assistant"):
        st.markdown(f"The assistant wants to remember:\n\n**{pending_fact}**")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("✅ Confirm save", key="confirm_save"):
                save_memory(pending_user, pending_fact)
                del st.session_state.pending_memory
                st.rerun()
        with col2:
            if st.button("❌ Discard", key="discard_memory"):
                del st.session_state.pending_memory
                st.rerun()

if user_input := st.chat_input("Type a message..."):
    st.session_state.messages.append({"role": "user", "content": user_input})

    # Build a fresh agent so the latest memories appear in its system prompt
    agent = build_agent(user_id, query=user_input)
    config = {"configurable": {"thread_id": st.session_state.thread_id}}
    result = agent.invoke(
        {"messages": [{"role": "user", "content": user_input}]},
        config=config,
    )
    reply = result["messages"][-1].content

    st.session_state.messages.append({"role": "assistant", "content": reply})

    # Transfer any staged memory (set by the tool in a thread-pool thread) to session state
    pending = pop_pending_memory()
    if pending:
        st.session_state.pending_memory = pending
    # Invalidate cached label so it refreshes on next rerun
    st.session_state.pop(f"label_{st.session_state.thread_id}", None)
    st.rerun()  # replay history and refresh the sidebar memory list