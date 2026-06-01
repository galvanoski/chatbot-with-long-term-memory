from uuid import uuid4

from dotenv import load_dotenv
import streamlit as st

load_dotenv()

from agent import build_agent
from memory_db import get_memories

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
        st.rerun()

    st.caption(f"Current thread: `{st.session_state.thread_id[:8]}...`")

    st.header("Long-term memory")
    memories = get_memories(user_id)
    if memories:
        for fact in memories:
            st.write(f"- {fact}")
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
    st.rerun()  # replay history and refresh the sidebar memory list