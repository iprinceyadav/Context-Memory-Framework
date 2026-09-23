from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    DATABASE_URL: str
    LLM_PROVIDER: str = "gemini"
    LLM_API_KEY: str = ""
    MODEL_NAME: str = "gemini-1.5-pro-latest"
    
    ENVIRONMENT: str = "development"
    MAX_QUERY_ROWS: int = 100
    MAX_RETRIES: int = 3
    ENABLE_LOCAL_LLM: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
