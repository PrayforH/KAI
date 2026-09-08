"""Optional semantic/extraction clients shared by composition roots."""

from harness.config import Settings
from harness.memory_bank.embedding import OpenAIEmbeddingClient
from harness.memory_bank.extraction import MemoryExtractor


def embedding_client(settings: Settings) -> OpenAIEmbeddingClient | None:
    if not settings.memory_embedding_base_url:
        return None
    return OpenAIEmbeddingClient(
        settings.memory_embedding_base_url,
        settings.memory_embedding_api_key,
        settings.memory_embedding_model,
        dimensions=settings.memory_embedding_dimensions,
    )


def extraction_client(settings: Settings) -> MemoryExtractor | None:
    if not settings.memory_extraction_enabled:
        return None
    if not all(
        (
            settings.memory_extraction_base_url,
            settings.memory_extraction_model,
            settings.memory_extraction_api_key.get_secret_value(),
            settings.memory_extraction_since,
        )
    ):
        raise ValueError(
            "memory extraction requires explicit model, endpoint, key and rollout date"
        )
    return MemoryExtractor(
        settings.memory_extraction_base_url,
        settings.memory_extraction_api_key,
        settings.memory_extraction_model,
    )
