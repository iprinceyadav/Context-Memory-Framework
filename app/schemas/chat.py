from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any


class ChatRequest(BaseModel):
    natural_query: str
    conversation_id: Optional[str] = None
    connection_id: Optional[str] = None
    db_type: str = "postgresql"
    host: Optional[str] = "localhost"
    port: Optional[int] = 5432
    database_name: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = ""
    organization_id: str = "default-org"
    user_id: str = "default-user"


class MemoryContext(BaseModel):
    memory_id: str
    content: str
    type: str
    score: float


class ContextUsed(BaseModel):
    memories: List[MemoryContext] = Field(default_factory=list)
    schema_tables: List[str] = Field(default_factory=list)
    conversation_turns: int = 0
    tokens_used: int = 0
    token_budget: int = 0


class ChatResponse(BaseModel):
    conversation_id: str
    message_id: str
    natural_response: str
    sql_query: Optional[str] = None
    data: Optional[List[Dict[str, Any]]] = None
    context_used: Optional[ContextUsed] = None
    context_debug: Optional[Dict[str, Any]] = None
    extracted_memories: Optional[List[Dict[str, Any]]] = None


class ContextDebugInfo(BaseModel):
    query: str
    retrieved_memories: List[Dict[str, Any]] = Field(default_factory=list)
    excluded_memories: List[Dict[str, Any]] = Field(default_factory=list)
    similarity_scores: List[float] = Field(default_factory=list)
    keyword_scores: List[float] = Field(default_factory=list)
    combined_scores: List[float] = Field(default_factory=list)
    token_budget: int = 0
    tokens_used: int = 0
    schema_tables: List[str] = Field(default_factory=list)
    conversation_turns: int = 0
    final_prompt_preview: Optional[str] = None
