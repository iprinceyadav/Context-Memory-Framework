from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.engine import Engine
from app.models.database_connection import DatabaseConnection
from app.models.database_schema import DatabaseSchema
from app.services.encryption import decrypt
import hashlib
import json
class DatabaseConnector:
    def __init__(self):
        self._engines: dict[str, Engine] = {}

    def connection_url(self, db_type: str, username: str, password: str, host: str, port: int, db_name: str) -> str:
        if db_type == "postgresql":
            return f"postgresql://{username}:{password}@{host}:{port}/{db_name}"
        elif db_type == "mysql":
            return f"mysql+pymysql://{username}:{password}@{host}:{port}/{db_name}"
        elif db_type == "sqlite":
            return f"sqlite:///{db_name}"
        else:
            raise ValueError(f"Unsupported database type: {db_type}")

    def ping(self, db_type: str, username: str, password: str, host: str, port: int, db_name: str) -> bool:
        try:
            url = self.connection_url(db_type, username, password, host, port, db_name)
            engine = create_engine(url, connect_args={"connect_timeout": 5} if db_type == "postgresql" else {})

            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception as e:
            raise Exception(f"Connection failed: {str(e)}")

    def execute(self, db_type: str, username: str, password: str, host: str, port: int, db_name: str, query: str) -> list[dict]:
        try:
            url = self.connection_url(db_type, username, password, host, port, db_name)
            engine = create_engine(url, connect_args={"connect_timeout": 5} if db_type == "postgresql" else {})

            with engine.connect() as conn:
                result = conn.execute(text(query))

                if result.returns_rows:
                    columns = result.keys()
                    return [dict(zip(columns, row)) for row in result.fetchall()]
                else:
                    conn.commit()
                    return [{"status": "success", "message": "Query executed successfully, no rows returned."}]
        except Exception as e:
            raise Exception(f"Query execution failed: {str(e)}")

    def fingerprint(self, db_type: str, username: str, host: str, port: int, db_name: str) -> str:
        identity = f"{db_type}://{username}@{host}:{port}/{db_name}"
        return hashlib.sha256(identity.encode()).hexdigest()

    def sync_schema(self, db_type: str, username: str, password: str, host: str, port: int, db_name: str, platform_db: Session) -> dict:
        try:
            url = self.connection_url(db_type, username, password, host, port, db_name)
            engine = create_engine(url, connect_args={"connect_timeout": 5} if db_type == "postgresql" else {})

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
            schema_key = self.fingerprint(db_type, username, host, port, db_name)

            existing_schema = platform_db.query(DatabaseSchema).filter(DatabaseSchema.connection_hash == schema_key).first()
            if existing_schema:
                existing_schema.schema_json = schema_json_str
            else:
                new_schema = DatabaseSchema(
                    connection_hash=schema_key,
                    schema_json=schema_json_str
                )
                platform_db.add(new_schema)
            platform_db.commit()

            return schema_data
        except Exception as e:
            raise Exception(f"Schema extraction failed: {str(e)}")

    def engine_for(self, record: DatabaseConnection) -> Engine:
        if record.id in self._engines:
            return self._engines[record.id]

        password = decrypt(record.encrypted_password)
        url = self.connection_url(
            db_type=record.db_type,
            username=record.username,
            password=password,
            host=record.host,
            port=record.port,
            db_name=record.database_name
        )

        engine = create_engine(url)
        self._engines[record.id] = engine
        return engine

    def session_for(self, record: DatabaseConnection) -> Session:
        engine = self.engine_for(record)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        return SessionLocal()

connector = DatabaseConnector()
