from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List, Dict, Any
from datetime import datetime


class MemoryBase(BaseModel):
    memory_type: str
    content: str
    subject: Optional[str] = None
    scope: str = "personal"
    visibility: str = "personal"


class MemoryCreate(MemoryBase):
    connection_id: Optional[str] = None
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    importance: int = Field(default=5, ge=1, le=10)
    source_message_id: Optional[str] = None
    source_conversation_id: Optional[str] = None
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    auto_approve: bool = False


class MemoryUpdate(BaseModel):
    content: Optional[str] = None
    subject: Optional[str] = None
    scope: Optional[str] = None
    visibility: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    importance: Optional[int] = Field(default=None, ge=1, le=10)
    status: Optional[str] = None
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    change_reason: Optional[str] = None


class MemoryResponse(MemoryBase):
    id: str
    organization_id: str
    user_id: str
    connection_id: Optional[str] = None
    confidence: float
    importance: int
    source_message_id: Optional[str] = None
    source_conversation_id: Optional[str] = None
    schema_hash: Optional[str] = None
    status: str
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    supersedes_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemorySearchRequest(BaseModel):
    query: str
    organization_id: str = "default-org"
    user_id: str = "default-user"
    memory_types: Optional[List[str]] = None
    connection_id: Optional[str] = None
    limit: int = Field(default=10, ge=1, le=100)
    include_superseded: bool = False
    only_approved: bool = True


class MemorySearchResult(BaseModel):
    memory_id: str
    memory_type: str
    subject: Optional[str] = None
    content: str
    status: str
    scope: str
    visibility: str
    importance: int
    vector_similarity: float
    keyword_relevance: float
    recency_score: float
    importance_score: float
    type_relevance: float
    combined_score: float


class ExtractedMemory(BaseModel):
    type: str
    subject: str
    content: str
    scope: str
    visibility: str
    confidence: float = Field(ge=0.0, le=1.0)
    importance: int = Field(ge=1, le=10)


class MemoryExtractionResult(BaseModel):
    memories: List[ExtractedMemory]


class MemoryVersionResponse(BaseModel):
    id: str
    memory_id: str
    version_number: int
    previous_content: Optional[str] = None
    updated_content: str
    changed_by: str
    change_reason: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemoryAuditLogResponse(BaseModel):
    id: str
    memory_id: str
    action: str
    performed_by: str
    details: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemoryStatistics(BaseModel):
    total: int
    active: int
    pending: int
    approved: int
    rejected: int
    superseded: int
    by_type: Dict[str, int]
