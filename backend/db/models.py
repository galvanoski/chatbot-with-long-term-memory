import datetime
import uuid

from sqlalchemy import Column, String, DateTime, Text, JSON, Boolean, ForeignKey, create_engine
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String(255), unique=True, nullable=False)
    display_name = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    is_active = Column(Boolean, default=True)

    threads = relationship("Thread", back_populates="user", cascade="all, delete-orphan")
    brand_rules = relationship("BrandRule", back_populates="user", cascade="all, delete-orphan")


class Thread(Base):
    __tablename__ = "threads"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    title = Column(String(200), nullable=True)
    platform = Column(String(20), default="instagram")  # instagram | facebook
    status = Column(String(20), default="active")        # active | archived
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    user = relationship("User", back_populates="threads")


class BrandRule(Base):
    __tablename__ = "brand_rules"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    key = Column(String(100), nullable=False)     # e.g. "tone", "hashtags", "max_length"
    value = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    user = relationship("User", back_populates="brand_rules")


class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    thread_id = Column(String(36), ForeignKey("threads.id"), nullable=True)
    event_type = Column(String(50), nullable=False)  # agent_start | llm_call | tool_call | agent_complete
    payload = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


# ── Engine helpers ──

_engine = None


def get_engine(db_url: str = "sqlite+aiosqlite:///backend/geekcat.db"):
    global _engine
    if _engine is None:
        _engine = create_async_engine(db_url, echo=False)
    return _engine


async def init_db(db_url: str = "sqlite+aiosqlite:///backend/geekcat.db"):
    engine = get_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session(db_url: str = "sqlite+aiosqlite:///backend/geekcat.db"):
    engine = get_engine(db_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as session:
        yield session
