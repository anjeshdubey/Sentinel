"""Sentinel configuration — loads from sentinel.yaml + environment variables."""

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelConfig(BaseSettings):
    """LLM model configuration.

    Model names can be short aliases (e.g. "claude-sonnet") or full provider
    model IDs (e.g. "claude-sonnet-4-5-20250929"). The sentinel.gateway module
    resolves aliases at call time.
    """

    provider: str = "anthropic"
    default: str = "claude-sonnet"
    fallback: str = "claude-opus"
    temperature: float = 0.0
    max_tokens: int = 1024


class OutputConfig(BaseSettings):
    """Output configuration."""

    jsonl_path: Path = Path("./data/incidents.jsonl")
    console_format: Literal["rich", "json", "minimal"] = "rich"


# --- Retrieval configuration (Spike 1) ---


class QdrantConfig(BaseSettings):
    """Qdrant vector store configuration.

    Supports two modes:
    - embedded: Uses QdrantClient(path=...) — no Docker, runs in-process.
    - server: Uses QdrantClient(url=...) — requires Qdrant Docker/binary.
    """

    model_config = SettingsConfigDict(env_prefix="SENTINEL_QDRANT_")

    mode: Literal["embedded", "server"] = "embedded"
    path: str = "./data/qdrant"
    url: str = "http://localhost:6333"
    runbook_collection: str = "runbooks_v1"
    incident_collection: str = "incidents_v1"


class RetrievalConfig(BaseSettings):
    """Retrieval pipeline configuration.

    Controls embedding model, chunking strategy, and search parameters.
    """

    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384
    chunk_size_tokens: int = 512
    chunk_overlap_tokens: int = 64
    top_k: int = 3
    fetch_k: int = 10
    rerank: bool = False
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    score_threshold: float = 0.35


# --- Tools configuration (Spike 2) ---


class ToolsCacheConfig(BaseSettings):
    """Cache configuration for the tool provider."""

    ttl_seconds: dict[str, int] = Field(
        default_factory=lambda: {
            "ownership": 900,
            "deploys": 60,
            "dependencies": 900,
            "past_incidents": 0,
            "submit_resolution": 0,
            "get_escalation_advice": 0,
        },
        description="Per-tool TTL in seconds. 0 = single-flight only.",
    )
    max_entries: int = Field(
        default=512, description="Maximum cached entries (LRU eviction)"
    )
    single_flight: bool = Field(
        default=True, description="Enable concurrent-call dedup"
    )


class ToolsConfig(BaseSettings):
    """Configuration for the tool provider layer.

    Controls backend selection, data directory for JSON backends,
    per-tool timeouts, and caching behavior.
    """

    backend: Literal["json", "real"] = Field(
        default="json",
        description="Backend type: 'json' for local CMDB, 'real' for live APIs (Month 5)",
    )
    data_dir: str = Field(
        default="src/sentinel/data/cmdb",
        description="Directory containing CMDB JSON files",
    )
    timeouts_ms: dict[str, int] = Field(
        default_factory=lambda: {
            "ownership": 200,
            "deploys": 300,
            "dependencies": 200,
            "past_incidents": 500,
            "submit_resolution": 1000,
            "get_escalation_advice": 500,
        },
        description="Per-tool timeout in milliseconds",
    )
    cache: ToolsCacheConfig = Field(default_factory=ToolsCacheConfig)
    past_incidents_top_k: int = Field(
        default=3, description="Number of similar past incidents to retrieve"
    )


# --- Observability configuration (Spike 5) ---


class ObservabilityConfig(BaseSettings):
    """Configuration for Langfuse observability integration.

    When langfuse_enabled is False (default), the NullTracer is used
    with zero overhead — no Langfuse imports, no network calls.
    """

    langfuse_enabled: bool = Field(
        default=False,
        description="Enable Langfuse tracing (set via LANGFUSE_ENABLED env var)",
    )
    langfuse_public_key: str = Field(
        default="",
        description="Langfuse public key (pk_lf_...)",
    )
    langfuse_secret_key: str = Field(
        default="",
        description="Langfuse secret key (sk_lf_...)",
    )
    langfuse_host: str = Field(
        default="https://cloud.langfuse.com",
        description="Langfuse host URL",
    )
    flush_at: int = Field(
        default=100,
        description="Batch size: flush spans after this many are buffered",
    )
    flush_interval: float = Field(
        default=5.0,
        description="Flush interval in seconds",
    )


class Settings(BaseSettings):
    """Top-level Sentinel settings.

    Load order (highest priority wins):
    1. Environment variables (SENTINEL_MODEL__DEFAULT, etc.)
    2. sentinel.yaml in project root
    3. Defaults defined here

    Anthropic auth is handled separately by the sentinel.gateway module
    (reads ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL).
    """

    model_config = SettingsConfigDict(
        env_prefix="SENTINEL_",
        env_nested_delimiter="__",
    )

    model: ModelConfig = Field(default_factory=ModelConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    qdrant: QdrantConfig = Field(default_factory=QdrantConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    log_level: str = "INFO"


def load_settings(config_path: Path | None = None) -> Settings:
    """Load settings, optionally from a YAML file.

    For Week 1, we rely on env vars and defaults.
    YAML loading will be added when the config surface grows.
    """
    # Future: parse sentinel.yaml and merge
    return Settings()
