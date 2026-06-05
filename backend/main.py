"""The Geek Cat — Multi-Agent Marketing Pipeline Backend.

FastAPI entry point that wires together the LangGraph agent,
the MemoryManager, and the 6-hook Middleware.
"""

import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import init_routes, router as api_router
from backend.api.schemas import HealthResponse
from backend.graph.builder import build_marketing_graph
from backend.middleware.geekcat import GeekCatMiddleware

# ── Load environment ──
load_dotenv()

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("geekcat")


def _check_chroma_store(chroma_store: object) -> bool:
    try:
        client = getattr(chroma_store, "_client", None)
        heartbeat = getattr(client, "heartbeat", None)
        if callable(heartbeat):
            heartbeat()
            return True

        list_collections = getattr(client, "list_collections", None)
        if callable(list_collections):
            list_collections()
            return True

        return False
    except Exception:
        return False


def _check_sqlite_path(db_path: Path) -> bool:
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


def _build_health_response(chroma_store: object, sqlite_paths: list[Path]) -> HealthResponse:
    checks = {
        "chroma": _check_chroma_store(chroma_store),
        "sqlite": all(_check_sqlite_path(path) for path in sqlite_paths),
        "openrouter_api_key": bool(os.getenv("OPENROUTER_API_KEY")),
    }
    status = "ok" if all(checks.values()) else "degraded"
    return HealthResponse(status=status, service="the-geekcat", checks=checks)


# ── App factory ──

def create_app() -> FastAPI:
    from backend.api.deps import get_chroma_store, get_memory_manager, get_middleware
    from backend.api.deps import get_bm25

    chroma_store = get_chroma_store()
    bm25 = get_bm25()
    memory = get_memory_manager()
    middleware = get_middleware()

    sqlite_paths = [
        Path(__file__).resolve().parents[1] / "threads.db",
        Path(getattr(bm25, "_db_path", Path(__file__).resolve().parents[1] / "fts5_index.db")),
    ]

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        logger.info("Initialising The Geek Cat backend...")
        graph = await build_marketing_graph(middleware=middleware)
        init_routes(graph=graph, middleware=middleware, memory=memory)
        logger.info("Backend initialised successfully")
        yield

    app = FastAPI(
        title="The Geek Cat — Marketing AI Agent",
        description="Multi-agent pipeline for autonomous POD marketing copy generation",
        version="0.1.0",
        docs_url="/docs",
        lifespan=lifespan,
    )

    # CORS — allow Nuxt frontend
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router)

    @app.get("/health", response_model=HealthResponse)
    def health():
        health_response = _build_health_response(chroma_store, sqlite_paths)
        if health_response.status != "ok":
            raise HTTPException(status_code=503, detail=health_response.model_dump())
        return health_response

    return app


app = create_app()
