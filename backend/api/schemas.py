from typing import Any, Optional

from pydantic import BaseModel


# ── Request schemas ──

class ThreadCreateRequest(BaseModel):
    user_id: str


class MessageSendRequest(BaseModel):
    user_id: str
    content: str


class ApprovalRequest(BaseModel):
    user_id: str
    feedback: Optional[str] = None
    edited_copy: Optional[str] = None
    edited_parts: Optional[dict[str, str]] = None


class RegenerateRequest(BaseModel):
    user_id: str
    instruction: Optional[str] = None


class BrandRuleSaveRequest(BaseModel):
    user_id: str
    key: str
    value: str


# ── Response schemas ──

class ThreadResponse(BaseModel):
    thread_id: str
    user_id: str


class MessageResponse(BaseModel):
    thread_id: str
    draft_copy_de: Optional[str] = None
    approval_status: Optional[str] = None
    is_interrupted: bool = False
    validation: Optional[dict] = None
    copy_metadata: Optional[dict] = None


class ApprovalResponse(BaseModel):
    thread_id: str
    status: str
    publication_result: Optional[dict] = None


class MemoryItem(BaseModel):
    id: str
    text: str
    metadata: Optional[dict] = None


class MemoryListResponse(BaseModel):
    user_id: str
    total: int
    items: list[MemoryItem]


class BrandRuleResponse(BaseModel):
    rules: dict[str, str]


class ProductSearchResult(BaseModel):
    id: str
    text: str
    score: float
    metadata: Optional[dict] = None


class ProductCatalogItem(BaseModel):
    id: Optional[str] = None
    text: str
    metadata: dict[str, Any]


class ProductCatalogBulkLoadRequest(BaseModel):
    items: list[ProductCatalogItem]


class ErrorResponse(BaseModel):
    detail: str
    error_code: Optional[str] = None
