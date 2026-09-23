from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models.database_schema import DatabaseSchema
import json

# Connect to the internal platform database
# This uses the default SQLite database path used by the app
DATABASE_URL = "sqlite:///./cmf_platform.db"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def read_database_info():
    print(f"Connecting to {DATABASE_URL}...\n")
    session = SessionLocal()
    
    try:
        # Fetch all saved schemas from the internal database
        schemas = session.query(DatabaseSchema).all()
        
        if not schemas:
            print("No database schemas have been synced yet.")
            return

        print(f"Found {len(schemas)} synced database connection(s)!\n")
        
        for idx, schema in enumerate(schemas):
            print(f"--- Connection {idx + 1} ---")
            print(f"Connection Hash ID: {schema.connection_hash}")
            
            # Parse the JSON string back into a dictionary
            schema_data = json.loads(schema.schema_json)
            
            print(f"Total Tables Synced: {len(schema_data)}")
            print("Tables and Columns:")
            
            for table_name, columns in schema_data.items():
                print(f"\n  > Table: {table_name}")
                for col in columns:
                    print(f"      - {col['name']} ({col['type']})")
            
            print("\n" + "="*40 + "\n")
            
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    read_database_info()
