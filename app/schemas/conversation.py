from pydantic import BaseModel, ConfigDict
from typing import Optional, Any
from datetime import datetime


class ConversationCreate(BaseModel):
    title: Optional[str] = None
    connection_id: Optional[str] = None


class ConversationResponse(BaseModel):
    id: str
    title: Optional[str] = None
    organization_id: str
    user_id: str
    connection_id: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MessageResponse(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: str
    sql_query: Optional[str] = None
    query_results: Optional[Any] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
