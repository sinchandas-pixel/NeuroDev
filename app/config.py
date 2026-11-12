from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --------------------------------------------------
    # Application
    # --------------------------------------------------
    APP_NAME: str = "NeuroDev"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # --------------------------------------------------
    # Groq LLM
    # --------------------------------------------------
    GROQ_API_KEY: str
    GROQ_MODEL: str = "openai/gpt-oss-120b"
    GROQ_RESPONSE_MODEL: str = "openai/gpt-oss-20b"
    GROQ_VISION_MODEL: str = "qwen/qwen3.8-27b"
    GROQ_TEMPERATURE: float = 0.2

    # --------------------------------------------------
    # Replicate - BGE-M3 Embeddings
    # --------------------------------------------------
    REPLICATE_API_TOKEN: str
    EMBEDDING_MODEL: str = "BAAI/bge-m3"

    # --------------------------------------------------
    # Redis - Short Term Memory
    # --------------------------------------------------
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_TTL: int = 86400

    # --------------------------------------------------
    # PostgreSQL - Structured Memory
    # --------------------------------------------------
    POSTGRES_URL: str
    POSTGRES_CONNECT_TIMEOUT: int = 3

    # --------------------------------------------------
    # pgvector
    # --------------------------------------------------
    VECTOR_COLLECTION: str = "neurodev_memory"
    VECTOR_DIMENSION: int = 1024

    # --------------------------------------------------
    # Application Settings
    # --------------------------------------------------
    MAX_RETRIEVAL_RESULTS: int = 5
    CONFIDENCE_THRESHOLD: float = 0.70

    # --------------------------------------------------
    # Account email and session settings
    # --------------------------------------------------
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = ""
    SMTP_USE_SSL: bool = False
    SMTP_STARTTLS: bool = True
    AUTH_PUBLIC_URL: str = "http://localhost:8000"
    AUTH_SESSION_DAYS: int = 14
    AUTH_VERIFICATION_HOURS: int = 24
    AUTH_VERIFICATION_MINUTES: int = 15
    AUTH_VERIFICATION_MAX_ATTEMPTS: int = 5
    ADMIN_EMAILS: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()