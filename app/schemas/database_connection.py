from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class DatabaseConnectionTest(BaseModel):
    db_type: str = "postgresql"
    host: str
    port: int = 5432
    database_name: str
    username: str
    password: str

class DatabaseConnectionCreate(DatabaseConnectionTest):
    name: str
    organization_id: str

class DatabaseConnectionResponse(BaseModel):
    id: str
    name: str
    db_type: str
    host: str
    port: int
    database_name: str
    username: str
    is_active: bool
    organization_id: str
    created_at: datetime
    
    class Config:
        from_attributes = True

class DirectQueryRequest(BaseModel):
    db_type: str = "postgresql"
    host: str
    port: int = 5432
    database_name: str
    username: str
    password: str
    query: str

class SyncSchemaRequest(DatabaseConnectionTest):
    pass

class AskQueryRequest(BaseModel):
    db_type: str = "postgresql"
    host: str
    port: int = 5432
    database_name: str
    username: str
    password: str
    natural_query: str
