from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.engine import Engine
from app.models.database_connection import DatabaseConnection
from app.models.database_schema import DatabaseSchema
from app.services.encryption import decrypt_password
import hashlib
import json
class DatabaseManager:
    """Service to handle dynamic tenant database connections."""
    
    def __init__(self):
        # Cache engines to avoid recreating them on every query
        self._engines: dict[str, Engine] = {}

    def build_connection_string(self, db_type: str, username: str, password: str, host: str, port: int, db_name: str) -> str:
        if db_type == "postgresql":
            return f"postgresql://{username}:{password}@{host}:{port}/{db_name}"
        elif db_type == "mysql":
            return f"mysql+pymysql://{username}:{password}@{host}:{port}/{db_name}"
        elif db_type == "sqlite":
            return f"sqlite:///{db_name}"
        else:
            raise ValueError(f"Unsupported database type: {db_type}")

    def test_connection(self, db_type: str, username: str, password: str, host: str, port: int, db_name: str) -> bool:
        """Tests if a connection can be established with the provided credentials."""
        try:
            conn_str = self.build_connection_string(db_type, username, password, host, port, db_name)
            engine = create_engine(conn_str, connect_args={"connect_timeout": 5} if db_type == "postgresql" else {})
            
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception as e:
            raise Exception(f"Connection failed: {str(e)}")

    def execute_direct_query(self, db_type: str, username: str, password: str, host: str, port: int, db_name: str, query: str) -> list[dict]:
        """Executes a direct query using provided credentials and returns results as a list of dicts."""
        try:
            conn_str = self.build_connection_string(db_type, username, password, host, port, db_name)
            engine = create_engine(conn_str, connect_args={"connect_timeout": 5} if db_type == "postgresql" else {})
            
            with engine.connect() as conn:
                result = conn.execute(text(query))
                
                # If it's not a SELECT query or returns no rows, just return success
                if result.returns_rows:
                    columns = result.keys()
                    return [dict(zip(columns, row)) for row in result.fetchall()]
                else:
                    conn.commit()
                    return [{"status": "success", "message": "Query executed successfully, no rows returned."}]
        except Exception as e:
            raise Exception(f"Query execution failed: {str(e)}")

    def generate_connection_hash(self, db_type: str, username: str, host: str, port: int, db_name: str) -> str:
        """Generates a unique hash for a database connection."""
        conn_string = f"{db_type}://{username}@{host}:{port}/{db_name}"
        return hashlib.sha256(conn_string.encode()).hexdigest()

    def extract_and_save_schema(self, db_type: str, username: str, password: str, host: str, port: int, db_name: str, cmf_db: Session) -> dict:
        """Extracts schema from the target DB and saves it to the CMF platform DB."""
        try:
            conn_str = self.build_connection_string(db_type, username, password, host, port, db_name)
            engine = create_engine(conn_str, connect_args={"connect_timeout": 5} if db_type == "postgresql" else {})
            
            inspector = inspect(engine)
            schema_data = {}
            
            for table_name in inspector.get_table_names():
                columns = []
                for col in inspector.get_columns(table_name):
                    columns.append({
                        "name": col["name"],
                        "type": str(col["type"])
                    })
                schema_data[table_name] = columns
                
            schema_json_str = json.dumps(schema_data)
            conn_hash = self.generate_connection_hash(db_type, username, host, port, db_name)
            
            # Save or update in CMF DB
            existing_schema = cmf_db.query(DatabaseSchema).filter(DatabaseSchema.connection_hash == conn_hash).first()
            if existing_schema:
                existing_schema.schema_json = schema_json_str
            else:
                new_schema = DatabaseSchema(
                    connection_hash=conn_hash,
                    schema_json=schema_json_str
                )
                cmf_db.add(new_schema)
            cmf_db.commit()
            
            return schema_data
        except Exception as e:
            raise Exception(f"Schema extraction failed: {str(e)}")

    def get_engine(self, db_conn_model: DatabaseConnection) -> Engine:
        """Returns a SQLAlchemy engine for a specific DatabaseConnection."""
        if db_conn_model.id in self._engines:
            return self._engines[db_conn_model.id]
        
        password = decrypt_password(db_conn_model.encrypted_password)
        conn_str = self.build_connection_string(
            db_type=db_conn_model.db_type,
            username=db_conn_model.username,
            password=password,
            host=db_conn_model.host,
            port=db_conn_model.port,
            db_name=db_conn_model.database_name
        )
        
        engine = create_engine(conn_str)
        self._engines[db_conn_model.id] = engine
        return engine

    def get_session(self, db_conn_model: DatabaseConnection) -> Session:
        """Returns a new Session for a specific DatabaseConnection."""
        engine = self.get_engine(db_conn_model)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        return SessionLocal()

# Global instance
db_manager = DatabaseManager()
