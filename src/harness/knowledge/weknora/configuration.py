"""Settings for the WeKnora gateway. Empty base URL disables the engine."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import SecretStr


@dataclass(frozen=True)
class WeknoraSettings:
    base_url: str = ""
    email: str = ""
    password: SecretStr = SecretStr("")
    timeout_seconds: float = 30.0
    embedding_model: str = ""
    summary_model_id: str = ""
    wiki_synthesis_model_id: str = ""
    wiki_max_pages_per_ingest: int = 12
    rag_search_limit: int = 25
    # Product-level kb_type -> WeKnora indexing strategy switches.
    kb_type_strategies: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: {
            "rag": ("vector_enabled", "keyword_enabled"),
            "wiki": ("wiki_enabled", "graph_enabled"),
            "hybrid": ("vector_enabled", "keyword_enabled", "wiki_enabled", "graph_enabled"),
        }
    )

    @property
    def enabled(self) -> bool:
        return bool(self.base_url.strip())
