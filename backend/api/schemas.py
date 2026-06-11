from typing import Any, Optional

from pydantic import BaseModel, Field


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
    message_id: Optional[str] = None


class RegenerateRequest(BaseModel):
    user_id: str
    instruction: Optional[str] = None


class ImagePromptRequest(BaseModel):
    user_id: str
    instruction: str = "a cat programmer logo"
    silent: bool = False


class SEORequest(BaseModel):
    user_id: str
    instruction: str = "Generate SEO metadata for my product"


class ImageGenerationRequest(BaseModel):
    user_id: str
    prompt: str = ""
    source_message_id: Optional[str] = None


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


# ── Shared response schemas ──

class ThreadMessageResponse(BaseModel):
    id: str
    role: str
    content: str
    created_at: str
    pending_approval: bool = False
    usage: Optional[dict] = None
    rag_trace: Optional[list[dict]] = None
    image_url: Optional[str] = None
    seo_metadata: Optional[dict] = None
    is_image_prompt: Optional[bool] = False
    rating: Optional[str] = None


class ThreadSourceResponse(BaseModel):
    label: str
    url: Optional[str] = None
    type: Optional[str] = None


class PendingCopyResponse(BaseModel):
    content: str
    hashtags: list[str] = Field(default_factory=list)
    product_name: Optional[str] = None
    product_url: Optional[str] = None
    sources: list[ThreadSourceResponse] = Field(default_factory=list)
    parts: Optional[dict[str, Any]] = None


class ThreadListItemResponse(BaseModel):
    id: str
    title: Optional[str] = None
    created_at: str
    updated_at: str
    status: str
    message_count: int


class ThreadDetailResponse(BaseModel):
    id: str
    user_id: Optional[str] = None
    title: Optional[str] = None
    created_at: str
    updated_at: str
    status: str
    messages: list[ThreadMessageResponse] = Field(default_factory=list)
    pending_copy: Optional[PendingCopyResponse] = None


class ThreadStateResponse(BaseModel):
    status: str
    messages: list[ThreadMessageResponse] = Field(default_factory=list)
    pending_copy: Optional[PendingCopyResponse] = None


class ThreadActionResponse(BaseModel):
    title: Optional[str] = None
    status: str
    messages: list[ThreadMessageResponse] = Field(default_factory=list)
    pending_copy: Optional[PendingCopyResponse] = None


class StatusResponse(BaseModel):
    status: str


class DeleteMemoryResponse(BaseModel):
    status: str
    doc_id: str


class BrandRuleSaveResponse(BaseModel):
    status: str
    key: str


class ProductCatalogBulkLoadResponse(BaseModel):
    loaded: int


class HealthResponse(BaseModel):
    status: str
    service: str
    checks: dict[str, bool]


class DeleteThreadResponse(BaseModel):
    status: str
    thread_id: str
