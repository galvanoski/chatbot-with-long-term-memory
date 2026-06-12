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
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from backend.api.routes import init_routes, router as api_router
from backend.api.schemas import HealthResponse
from backend.graph.builder import build_marketing_graph
from backend.middleware.geekcat import GeekCatMiddleware

# ── Load environment ──
# Try current dir first, then fall back to project root
load_dotenv()
_dotenv_path = Path(__file__).resolve().parents[1] / ".env"
if _dotenv_path.exists():
    load_dotenv(_dotenv_path, override=True)

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("geekcat")

# ── LangSmith / LLM Observability ──
# When LANGCHAIN_TRACING_V2=true + LANGSMITH_API_KEY are set in .env,
# LangChain automatically traces all ChatOpenAI calls to LangSmith.
# The raw OpenAI client (image generation) is wrapped separately.
if os.getenv("LANGCHAIN_TRACING_V2") is None:
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
if os.getenv("LANGSMITH_PROJECT") is None:
    os.environ.setdefault("LANGSMITH_PROJECT", "the-geekcat-marketing-agent")
print("[main.py] Starting LangSmith setup...", flush=True)
api_key = os.getenv("LANGSMITH_API_KEY")
print(f"[main.py] LANGSMITH_API_KEY={'*** set ***' if api_key else 'NOT SET'}", flush=True)
print(f"[main.py] LANGCHAIN_TRACING_V2={os.getenv('LANGCHAIN_TRACING_V2')}", flush=True)
print(f"[main.py] LANGSMITH_ENDPOINT={os.getenv('LANGSMITH_ENDPOINT')}", flush=True)

if api_key:
    # LangChain auto-tracing uses LANGCHAIN_* env vars; mirror LANGSMITH_* into them
    os.environ.setdefault("LANGCHAIN_API_KEY", api_key)
    os.environ.setdefault("LANGCHAIN_ENDPOINT",
                          os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com"))
    try:
        from langsmith import Client as LangSmithClient
        kwargs = {}
        if os.getenv("LANGSMITH_ENDPOINT"):
            kwargs["api_url"] = os.environ["LANGSMITH_ENDPOINT"]
        ls_client = LangSmithClient(**kwargs)
        logger.info("LangSmith tracing enabled — project=%s endpoint=%s",
                     os.environ["LANGSMITH_PROJECT"],
                     os.environ["LANGCHAIN_ENDPOINT"])
        print("[main.py] LangSmith tracing ENABLED", flush=True)
    except Exception as exc:
        logger.warning("LangSmith API key set but connection failed: %s — tracing disabled", exc)
        print(f"[main.py] LangSmith connection FAILED: {exc}", flush=True)
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
else:
    logger.info("LangSmith tracing disabled — set LANGSMITH_API_KEY in .env to enable")
    print("[main.py] LangSmith tracing DISABLED (no API key)", flush=True)


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

    # Serve generated images
    static_dir = Path(__file__).resolve().parent / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    (static_dir / "images").mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/health", response_model=HealthResponse)
    def health():
        health_response = _build_health_response(chroma_store, sqlite_paths)
        if health_response.status != "ok":
            raise HTTPException(status_code=503, detail=health_response.model_dump())
        return health_response

    return app


app = create_app()
