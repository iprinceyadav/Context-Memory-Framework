from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database.connection import get_db
from app.models.organization import Organization
from app.models.database_connection import DatabaseConnection
from app.models.database_schema import DatabaseSchema
from app.schemas.database_connection import DatabaseConnectionCreate, DatabaseConnectionResponse, DatabaseConnectionTest, DirectQueryRequest, SyncSchemaRequest, AskQueryRequest
from app.services.database_manager import db_manager
from app.services.llm_service import llm_service
from app.services.encryption import encrypt_password
from typing import List

router = APIRouter(prefix="/databases", tags=["Databases"])

@router.post("/test")
def test_database_connection(conn_data: DatabaseConnectionTest):
    """Test a database connection without saving it."""
    try:
        success = db_manager.test_connection(
            db_type=conn_data.db_type,
            username=conn_data.username,
            password=conn_data.password,
            host=conn_data.host,
            port=conn_data.port,
            db_name=conn_data.database_name
        )
        if success:
            return {"status": "success", "message": "Connection successful"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/connect", response_model=DatabaseConnectionResponse, status_code=status.HTTP_201_CREATED)
def connect_database(conn_data: DatabaseConnectionCreate, db: Session = Depends(get_db)):
    """Save a new database connection for an organization."""
    # Verify organization exists
    org = db.query(Organization).filter(Organization.id == conn_data.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Test the connection first
    try:
        db_manager.test_connection(
            db_type=conn_data.db_type,
            username=conn_data.username,
            password=conn_data.password,
            host=conn_data.host,
            port=conn_data.port,
            db_name=conn_data.database_name
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Cannot save invalid connection: {str(e)}")

    # Encrypt password and save
    encrypted_pw = encrypt_password(conn_data.password)
    
    new_db = DatabaseConnection(
        name=conn_data.name,
        db_type=conn_data.db_type,
        host=conn_data.host,
        port=conn_data.port,
        database_name=conn_data.database_name,
        username=conn_data.username,
        encrypted_password=encrypted_pw,
        organization_id=conn_data.organization_id
    )
    
    db.add(new_db)
    db.commit()
    db.refresh(new_db)
    return new_db

@router.get("/", response_model=List[DatabaseConnectionResponse])
def list_databases(organization_id: str, db: Session = Depends(get_db)):
    """List all database connections for a given organization."""
    org = db.query(Organization).filter(Organization.id == organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
        
    return db.query(DatabaseConnection).filter(DatabaseConnection.organization_id == organization_id).all()

@router.post("/query-direct")
def query_direct_database(conn_data: DirectQueryRequest):
    """Execute a query directly against a database without saving the connection."""
    try:
        results = db_manager.execute_direct_query(
            db_type=conn_data.db_type,
            username=conn_data.username,
            password=conn_data.password,
            host=conn_data.host,
            port=conn_data.port,
            db_name=conn_data.database_name,
            query=conn_data.query
        )
        return {"status": "success", "data": results}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/sync-schema")
def sync_database_schema(conn_data: SyncSchemaRequest, db: Session = Depends(get_db)):
    """Connect to a database, extract its schema, and save it."""
    try:
        schema = db_manager.extract_and_save_schema(
            db_type=conn_data.db_type,
            username=conn_data.username,
            password=conn_data.password,
            host=conn_data.host,
            port=conn_data.port,
            db_name=conn_data.database_name,
            cmf_db=db
        )
        return {"status": "success", "message": "Schema extracted and saved successfully"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/ask")
def ask_natural_language_query(conn_data: AskQueryRequest, db: Session = Depends(get_db)):
    """Convert natural language to SQL using LLM and execute it."""
    try:
        # Get schema
        conn_hash = db_manager.generate_connection_hash(
            db_type=conn_data.db_type,
            username=conn_data.username,
            host=conn_data.host,
            port=conn_data.port,
            db_name=conn_data.database_name
        )
        
        schema_model = db.query(DatabaseSchema).filter(DatabaseSchema.connection_hash == conn_hash).first()
        if not schema_model:
            # Fallback: extract on the fly if not synced yet
            db_manager.extract_and_save_schema(
                db_type=conn_data.db_type,
                username=conn_data.username,
                password=conn_data.password,
                host=conn_data.host,
                port=conn_data.port,
                db_name=conn_data.database_name,
                cmf_db=db
            )
            schema_model = db.query(DatabaseSchema).filter(DatabaseSchema.connection_hash == conn_hash).first()
            if not schema_model:
                raise Exception("Failed to retrieve or generate schema.")

        schema_json_str = schema_model.schema_json

        # Generate SQL
        sql_query = llm_service.generate_sql(
            natural_query=conn_data.natural_query,
            schema_json_str=schema_json_str,
            db_type=conn_data.db_type
        )
        
        # Execute SQL
        results = db_manager.execute_direct_query(
            db_type=conn_data.db_type,
            username=conn_data.username,
            password=conn_data.password,
            host=conn_data.host,
            port=conn_data.port,
            db_name=conn_data.database_name,
            query=sql_query
        )
        
        return {
            "status": "success",
            "sql_query": sql_query,
            "data": results
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
