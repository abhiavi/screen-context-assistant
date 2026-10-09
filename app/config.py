from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Gateway (adraca-azure-01 ONLY - never route to pve's zombie LiteLLM)
    litellm_base_url: str = "http://${GATEWAY_HOST}:4000/v1"
    litellm_api_key: str = ""
    embedding_model: str = "text-embedding-004"
    vision_model: str = "qwen-vl-ocr"
    synthesis_model: str = "glm-4.7"

    # Local storage (aws-01 only - separate from azure-01's shared Qdrant)
    qdrant_host: str = "${BACKEND_HOST}"
    qdrant_port: int = 6333
    qdrant_collection: str = "screen-context"

    postgres_host: str = "${BACKEND_HOST}"
    postgres_port: int = 5432
    postgres_db: str = "screen_context"
    postgres_user: str = "screen_context"
    postgres_password: str = ""

    # Temporal knowledge-graph memory (upgrade roadmap "Next" phase,
    # Graphiti). Bolt protocol, not the 7474 browser UI port.
    neo4j_host: str = "${BACKEND_HOST}"
    neo4j_bolt_port: int = 7687
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""

    bind_host: str = "${BACKEND_HOST}"
    ingest_port: int = 8088
    rag_port: int = 8089

    # Transmission boundary (plan §5). Both default closed/off.
    upload_raw_frames: bool = False
    retain_raw_frames: bool = False
    raw_frame_retention_seconds: int = 300
    frame_vector_retention_days: int = 30

    @property
    def neo4j_bolt_uri(self) -> str:
        return f"bolt://{self.neo4j_host}:{self.neo4j_bolt_port}"

    @property
    def postgres_dsn(self) -> str:
        return (
            f"host={self.postgres_host} port={self.postgres_port} "
            f"dbname={self.postgres_db} user={self.postgres_user} "
            f"password={self.postgres_password}"
        )


settings = Settings()
