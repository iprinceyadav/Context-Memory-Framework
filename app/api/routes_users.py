from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database.connection import get_db
from app.models.user import User
from app.models.organization import Organization
from app.schemas.user import UserCreate, UserResponse

router = APIRouter(prefix="/users", tags=["Users"])

@router.post("/", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(user: UserCreate, db: Session = Depends(get_db)):
    # Check if organization exists
    org = db.query(Organization).filter(Organization.id == user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
        
    # Check if user email already exists
    db_user = db.query(User).filter(User.email == user.email).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Email already registered")
        
    # In a real app, hash the password
    fake_hashed_password = f"hashed_{user.password}"
    
    new_user = User(
        email=user.email,
        full_name=user.full_name,
        hashed_password=fake_hashed_password,
        role=user.role,
        organization_id=user.organization_id
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user
