from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database.connection import get_db
from app.models.organization import Organization
from app.schemas.organization import OrganizationCreate, OrganizationResponse
from typing import List

router = APIRouter(prefix="/organizations", tags=["Organizations"])

@router.post("/", response_model=OrganizationResponse, status_code=status.HTTP_201_CREATED)
def create_organization(org: OrganizationCreate, db: Session = Depends(get_db)):
    db_org = db.query(Organization).filter(Organization.name == org.name).first()
    if db_org:
        raise HTTPException(status_code=400, detail="Organization already exists")

    new_org = Organization(name=org.name, domain=org.domain)
    db.add(new_org)
    db.commit()
    db.refresh(new_org)
    return new_org

@router.get("/", response_model=List[OrganizationResponse])
def list_organizations(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(Organization).offset(skip).limit(limit).all()
