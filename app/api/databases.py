from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database.connection import get_db
from app.models.organization import Organization
from app.models.database_connection import DatabaseConnection
from app.models.database_schema import DatabaseSchema
from app.schemas.database_connection import DatabaseConnectionCreate, DatabaseConnectionResponse, DatabaseConnectionTest, DirectQueryRequest, SyncSchemaRequest, AskQueryRequest
from app.services.connector import connector
from app.services.llm import llm
from app.core.auth import ensure_organization
from app.core.config import settings
from app.services.access import mask_rows
from app.services.encryption import encrypt
from app.services.sql_guard import plan_sql
from typing import List, Optional

router = APIRouter(prefix="/databases", tags=["Databases"])

@router.post("/test")
def check_connection(payload: DatabaseConnectionTest):
    try:
        success = connector.ping(
            db_type=payload.db_type,
            username=payload.username,
            password=payload.password,
            host=payload.host,
            port=payload.port,
            db_name=payload.database_name
        )
        if success:
            return {"status": "success", "message": "Connection successful"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/connect", response_model=DatabaseConnectionResponse, status_code=status.HTTP_201_CREATED)
def connect(payload: DatabaseConnectionCreate, db: Session = Depends(get_db)):
    ensure_organization(db, payload.organization_id)

    try:
        connector.ping(
            db_type=payload.db_type,
            username=payload.username,
            password=payload.password,
            host=payload.host,
            port=payload.port,
            db_name=payload.database_name
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Cannot save invalid connection: {str(e)}")

    saved = (
        db.query(DatabaseConnection)
        .filter(
            DatabaseConnection.organization_id == payload.organization_id,
            DatabaseConnection.db_type == payload.db_type,
            DatabaseConnection.host == payload.host,
            DatabaseConnection.port == payload.port,
            DatabaseConnection.database_name == payload.database_name,
            DatabaseConnection.username == payload.username,
        )
        .first()
    )
    if saved:
        saved.name = payload.name
        saved.encrypted_password = encrypt(payload.password)
        saved.is_active = True
        connector.forget(saved.id)
    else:
        saved = DatabaseConnection(
            name=payload.name,
            db_type=payload.db_type,
            host=payload.host,
            port=payload.port,
            database_name=payload.database_name,
            username=payload.username,
            encrypted_password=encrypt(payload.password),
            organization_id=payload.organization_id
        )
        db.add(saved)

    db.commit()
    db.refresh(saved)
    return saved

@router.get("/", response_model=List[DatabaseConnectionResponse])
def list_connections(organization_id: str, db: Session = Depends(get_db)):
    org = db.query(Organization).filter(Organization.id == organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    return db.query(DatabaseConnection).filter(DatabaseConnection.organization_id == organization_id).all()

def _read_only(payload, sql: str, schema_json_str: Optional[str] = None) -> List[dict]:
    plan = plan_sql(sql, payload.db_type, schema_json_str)
    if not plan.ok:
        raise HTTPException(status_code=400, detail=plan.error)
    if plan.is_write:
        raise HTTPException(
            status_code=400,
            detail="This endpoint is read-only. Ask for changes in chat; they need admin approval.",
        )
    engine = connector.open_engine(
        db_type=payload.db_type,
        username=payload.username,
        password=payload.password,
        host=payload.host,
        port=payload.port,
        db_name=payload.database_name,
    )
    try:
        rows, _ = connector.read(engine, plan.sql, settings.MAX_QUERY_ROWS)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Query execution failed: {e}")
    finally:
        engine.dispose()
    masked, _ = mask_rows(rows)
    return masked

@router.post("/query-direct")
def run_query(payload: DirectQueryRequest):
    return {"status": "success", "data": _read_only(payload, payload.query)}

@router.post("/sync-schema")
def sync(payload: SyncSchemaRequest, db: Session = Depends(get_db)):
    try:
        connector.sync_schema(
            db_type=payload.db_type,
            username=payload.username,
            password=payload.password,
            host=payload.host,
            port=payload.port,
            db_name=payload.database_name,
            platform_db=db
        )
        return {"status": "success", "message": "Schema extracted and saved successfully"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/ask")
def ask(payload: AskQueryRequest, db: Session = Depends(get_db)):
    try:
        schema_key = connector.fingerprint(
            db_type=payload.db_type,
            username=payload.username,
            host=payload.host,
            port=payload.port,
            db_name=payload.database_name
        )

        schema_row = db.query(DatabaseSchema).filter(DatabaseSchema.connection_hash == schema_key).first()
        if not schema_row:
            connector.sync_schema(
                db_type=payload.db_type,
                username=payload.username,
                password=payload.password,
                host=payload.host,
                port=payload.port,
                db_name=payload.database_name,
                platform_db=db
            )
            schema_row = db.query(DatabaseSchema).filter(DatabaseSchema.connection_hash == schema_key).first()
            if not schema_row:
                raise Exception("Failed to retrieve or generate schema.")

        schema_json_str = schema_row.schema_json

        sql_query = llm.generate_sql(
            natural_query=payload.natural_query,
            schema_json_str=schema_json_str,
            db_type=payload.db_type
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "status": "success",
        "sql_query": sql_query,
        "data": _read_only(payload, sql_query, schema_json_str),
    }
