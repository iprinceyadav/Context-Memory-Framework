from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./cmf_platform.db"
    LLM_PROVIDER: str = "gemini"
    LLM_API_KEY: str = ""
    MODEL_NAME: str = "gemini-1.5-pro-latest"

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

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
