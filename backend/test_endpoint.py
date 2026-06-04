"""Test the get_thread logic by initializing components first."""
import sys, os, logging
sys.stdout.reconfigure(errors='replace')
sys.path.insert(0, ".")
logging.basicConfig(level=logging.WARNING)

from dotenv import load_dotenv
load_dotenv()

from backend.api.routes import init_routes, router
from backend.api.deps import get_chroma_store, get_memory_manager, get_middleware
from backend.graph.builder import build_marketing_graph

middleware = get_middleware()
graph = build_marketing_graph(middleware=middleware)
memory = get_memory_manager()
init_routes(graph=graph, middleware=middleware, memory=memory)

import backend.api.routes as routes

thread_id = "22524d77-71a3-4a05-b5d2-4106c8f12bb9"
user_id = "anon_948a09d9-0e47-48a8-8aa6-e44e75761e9c"

try:
    thread = routes._load_thread_record(thread_id, user_id)
    print(f"thread loaded: {thread is not None}")
    if thread:
        print(f"  status={thread.get('status')} msgs={len(thread.get('messages', []))}")

    config = {"configurable": {"thread_id": thread_id}}
    state = graph.get_state(config)
    print(f"graph state is not None: {state is not None}")
    print(f"state.values is not None: {state.values is not None}")

    if state and state.values:
        keys = list(state.values.keys())
        print(f"  state keys ({len(keys)}): {keys}")
        print(f"  approval_status: {state.values.get('approval_status')}")
        print(f"  draft_copy exists: {bool(state.values.get('draft_copy_de'))}")

        payload = routes._build_thread_payload(thread, state.values)
        print(f"payload built OK, keys: {list(payload.keys())}")
        print(f"payload status: {payload.get('status')}")
        print(f"payload messages count: {len(payload.get('messages', []))}")
    else:
        print("state.values is empty/None")

except Exception as exc:
    import traceback
    traceback.print_exc()
