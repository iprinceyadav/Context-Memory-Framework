from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./cmf_platform.db"
    LLM_PROVIDER: str = "gemini"
    LLM_API_KEY: str = ""
    MODEL_NAME: str = "gemini-2.5-flash"
    SUMMARY_MODEL_NAME: str = "gemini-3.5-flash-lite"
    EXTRACTION_MODEL_NAME: str = "gemini-3.5-flash-lite"

    ENVIRONMENT: str = "development"
    MAX_QUERY_ROWS: int = 100
    MAX_RETRIES: int = 3
    ENABLE_LOCAL_LLM: bool = False

    EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"
    MEMORY_MAX_RESULTS: int = 10
    MEMORY_TOKEN_BUDGET: int = 4000
    MEMORY_AUTO_EXTRACT: bool = True
    MEMORY_DEFAULT_STATUS: str = "pending"
    CONVERSATION_HISTORY_TURNS: int = 6

    RETRIEVAL_WEIGHT_VECTOR: float = 0.45
    RETRIEVAL_WEIGHT_KEYWORD: float = 0.20
    RETRIEVAL_WEIGHT_RECENCY: float = 0.15
    RETRIEVAL_WEIGHT_IMPORTANCE: float = 0.10
    RETRIEVAL_WEIGHT_TYPE: float = 0.10

    ENFORCE_READ_ONLY_SQL: bool = True
    ALLOW_DATA_CHANGES: bool = True
    WRITE_MAX_ROWS: int = 50
    SENSITIVE_COLUMNS: str = "password,passwd,secret,token,api_key,apikey,ssn,card_number,cvv,pin_code"

    VECTOR_STORE_URL: str = ""
    VECTOR_STORE_API_KEY: str = ""
    VECTOR_STORE_PATH: str = "./vector_store"
    UPLOAD_DIR: str = "./uploads"
    RERANK_ENABLED: bool = True
    RERANKER_MODEL: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    RERANK_WEIGHT: float = 0.6
    RERANK_MIN_SCORE: float = 0.0
    RAG_CANDIDATES: int = 30
    RAG_SCHEMA_FULL_TABLES: int = 12
    RAG_SCHEMA_TOP_TABLES: int = 8
    RAG_EXAMPLES: int = 3
    RAG_REWRITE_QUERY: bool = True

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
