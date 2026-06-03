"""The Geek Cat — Multi-Agent Marketing Pipeline Backend.

FastAPI entry point that wires together the LangGraph agent,
the MemoryManager, and the 6-hook Middleware.
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import init_routes, router as api_router
from backend.graph.builder import build_marketing_graph
from backend.memory.manager import MemoryManager
from backend.middleware.geekcat import GeekCatMiddleware

# ── Load environment ──
load_dotenv()

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("geekcat")


# ── App factory ──

def create_app() -> FastAPI:
    app = FastAPI(
        title="The Geek Cat — Marketing AI Agent",
        description="Multi-agent pipeline for autonomous POD marketing copy generation",
        version="0.1.0",
        docs_url="/docs",
    )

    # CORS — allow Nuxt frontend
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Initialise components ──
    logger.info("Initialising The Geek Cat backend...")

    from backend.api.deps import get_chroma_store, get_memory_manager, get_middleware

    memory = get_memory_manager()
    middleware = get_middleware()
    graph = build_marketing_graph(middleware=middleware)

    # Wire routes
    init_routes(graph=graph, middleware=middleware, memory=memory)
    app.include_router(api_router)

    logger.info("Backend initialised successfully")

    return app


app = create_app()


# ── Health check ──
@app.get("/health")
def health():
    return {"status": "ok", "service": "the-geek-cat"}
