from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime

class UserBase(BaseModel):
    email: EmailStr
    full_name: str
    role: str = "viewer"

class UserCreate(UserBase):
    password: str
    organization_id: str

class UserResponse(UserBase):
    id: str
    organization_id: str
    created_at: datetime
    
    class Config:
        from_attributes = True
