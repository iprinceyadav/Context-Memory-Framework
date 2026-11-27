from datetime import datetime
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field


class MemberCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=120)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=200)
    role: Literal["viewer", "analyst", "admin"] = "viewer"


class MemberUpdate(BaseModel):
    role: Literal["viewer", "analyst", "admin"]


class MemberOut(BaseModel):
    id: str
    full_name: str
    email: str
    role: str

    class Config:
        from_attributes = True


class GrantCreate(BaseModel):
    user_id: str
    action: Literal["update", "insert", "delete"]
    table_name: str = Field(default="*", min_length=1, max_length=120)
    connection_id: Optional[str] = None


class GrantOut(BaseModel):
    id: str
    user_id: str
    action: str
    table_name: str
    connection_id: Optional[str] = None
    granted_by: str
    created_at: datetime

    class Config:
        from_attributes = True


class ChangeRequestOut(BaseModel):
    id: str
    user_id: str
    requester: Optional[str] = None
    question: str
    sql: str
    action: str
    table_name: str
    affected_rows: int
    summary: str
    preview: Dict[str, Any] = Field(default_factory=dict)
    status: str
    reviewed_by: Optional[str] = None
    reviewer: Optional[str] = None
    review_note: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime
    reviewed_at: Optional[datetime] = None
    executed_at: Optional[datetime] = None


class ReviewRequest(BaseModel):
    note: Optional[str] = Field(default=None, max_length=500)
