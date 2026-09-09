from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Gateway (adraca-azure-01 ONLY - never route to pve's zombie LiteLLM)
    litellm_base_url: str = "http://100.125.158.117:4000/v1"
    litellm_api_key: str = ""
    embedding_model: str = "text-embedding-004"
    vision_model: str = "qwen-vl-ocr"
    synthesis_model: str = "glm-4.7"

    # Local storage (aws-01 only - separate from azure-01's shared Qdrant)
    qdrant_host: str = "100.96.7.56"
    qdrant_port: int = 6333
    qdrant_collection: str = "screen-context"

    postgres_host: str = "100.96.7.56"
    postgres_port: int = 5432
    postgres_db: str = "screen_context"
    postgres_user: str = "screen_context"
    postgres_password: str = ""

    bind_host: str = "100.96.7.56"
    ingest_port: int = 8088
    rag_port: int = 8089

    # Transmission boundary (plan §5). Both default closed/off.
    upload_raw_frames: bool = False
    retain_raw_frames: bool = False
    raw_frame_retention_seconds: int = 300
    frame_vector_retention_days: int = 30

    @property
    def postgres_dsn(self) -> str:
        return (
            f"host={self.postgres_host} port={self.postgres_port} "
            f"dbname={self.postgres_db} user={self.postgres_user} "
            f"password={self.postgres_password}"
        )


settings = Settings()
