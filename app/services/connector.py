from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.engine import Engine
from app.models.database_connection import DatabaseConnection
from app.models.database_schema import DatabaseSchema
from app.services.encryption import decrypt
import hashlib
import json

import sqlglot
from sqlglot import exp

from app.services.access import is_sensitive
from app.services.indexer import index_schema
from app.services.sql_guard import dialect_for

SAMPLE_TABLES = 200
SAMPLE_COLUMNS = 12
SAMPLE_SCAN = 1000
SAMPLE_VALUES = 20
_TEXT_TYPES = ("CHAR", "TEXT", "STRING", "ENUM", "CLOB")


def _is_text(column_type: str) -> bool:
    upper = (column_type or "").upper()
    return any(marker in upper for marker in _TEXT_TYPES)


class RowCountMismatch(Exception):
    def __init__(self, expected: int, changed: int):
        super().__init__(f"Expected to change {expected} row(s) but the statement touched {changed}; rolled back.")
        self.expected = expected
        self.changed = changed


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

    def open_engine(self, db_type: str, username: str, password: str, host: str, port: int, db_name: str) -> Engine:
        url = self.connection_url(db_type, username, password, host, port, db_name)
        return create_engine(url, connect_args={"connect_timeout": 5} if db_type == "postgresql" else {})

    def read(self, engine: Engine, query: str, max_rows: int) -> tuple[list[dict], bool]:
        with engine.connect() as conn:
            transaction = conn.begin()
            try:
                result = conn.execute(text(query))
                if not result.returns_rows:
                    return [], False
                columns = list(result.keys())
                fetched = result.fetchmany(max_rows + 1)
                rows = [dict(zip(columns, row)) for row in fetched[:max_rows]]
                return rows, len(fetched) > max_rows
            finally:
                transaction.rollback()

    def count(self, engine: Engine, query: str) -> int:
        rows, _ = self.read(engine, query, 1)
        if not rows:
            return 0
        return int(next(iter(rows[0].values())) or 0)

    def write(self, engine: Engine, query: str, expected_rows: int) -> int:
        with engine.begin() as conn:
            result = conn.execute(text(query))
            changed = result.rowcount
            if changed not in (-1, expected_rows):
                raise RowCountMismatch(expected_rows, changed)
            return expected_rows if changed == -1 else changed

    def fingerprint(self, db_type: str, username: str, host: str, port: int, db_name: str) -> str:
        identity = f"{db_type}://{username}@{host}:{port}/{db_name}"
        return hashlib.sha256(identity.encode()).hexdigest()

    def sync_schema(self, db_type: str, username: str, password: str, host: str, port: int, db_name: str, platform_db: Session) -> dict:
        engine = None
        try:
            engine = self.open_engine(db_type, username, password, host, port, db_name)
            inspector = inspect(engine)
            schema_data = {}

            for table_name in inspector.get_table_names():
                references = {}
                try:
                    for key in inspector.get_foreign_keys(table_name):
                        for local, remote in zip(key.get("constrained_columns") or [], key.get("referred_columns") or []):
                            references[local] = f"{key.get('referred_table')}.{remote}"
                except Exception:
                    pass
                columns = []
                for col in inspector.get_columns(table_name):
                    entry = {"name": col["name"], "type": str(col["type"])}
                    if col["name"] in references:
                        entry["references"] = references[col["name"]]
                    columns.append(entry)
                schema_data[table_name] = columns

            self._sample_values(engine, db_type, schema_data)

            schema_json_str = json.dumps(schema_data)
            schema_key = self.fingerprint(db_type, username, host, port, db_name)

            existing_schema = platform_db.query(DatabaseSchema).filter(DatabaseSchema.connection_hash == schema_key).first()
            if existing_schema:
                existing_schema.schema_json = schema_json_str
            else:
                platform_db.add(DatabaseSchema(connection_hash=schema_key, schema_json=schema_json_str))
            platform_db.commit()

            index_schema(schema_key, schema_data)
            return schema_data
        except Exception as e:
            raise Exception(f"Schema extraction failed: {str(e)}")
        finally:
            if engine is not None:
                engine.dispose()

    def _sample_values(self, engine: Engine, db_type: str, schema: dict) -> None:
        dialect = dialect_for(db_type)
        for table, columns in list(schema.items())[:SAMPLE_TABLES]:
            candidates = [
                column for column in columns
                if _is_text(column["type"]) and "references" not in column and not is_sensitive(column["name"])
            ][:SAMPLE_COLUMNS]
            for column in candidates:
                name = exp.column(column["name"], quoted=True)
                scan = sqlglot.select(name.copy()).from_(exp.table_(table, quoted=True)).limit(SAMPLE_SCAN)
                query = sqlglot.select(name.copy()).distinct().from_(scan.subquery("sample")).limit(SAMPLE_VALUES + 1)
                try:
                    rows, _ = self.read(engine, query.sql(dialect), SAMPLE_VALUES + 1)
                except Exception:
                    continue
                values = sorted({str(value) for row in rows for value in row.values() if value is not None})
                if 0 < len(values) <= SAMPLE_VALUES and all(len(value) <= 40 for value in values):
                    column["values"] = values

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

    def forget(self, connection_id: str) -> None:
        engine = self._engines.pop(connection_id, None)
        if engine is not None:
            engine.dispose()

    def session_for(self, record: DatabaseConnection) -> Session:
        engine = self.engine_for(record)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        return SessionLocal()

connector = DatabaseConnector()
