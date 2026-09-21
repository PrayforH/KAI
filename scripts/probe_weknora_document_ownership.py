"""Probe WeKnora's document detail payload from inside the API container.

Answers one question the ownership check depends on: does the remote document
detail report which knowledge base it belongs to? Prints field names and base
identity only — never credentials or document content.
"""

import asyncio
import json

from harness.config import Settings
from harness.knowledge.weknora import WeknoraKnowledgeEngine, WeknoraSettings


def build() -> WeknoraKnowledgeEngine:
    app = Settings()
    return WeknoraKnowledgeEngine(
        WeknoraSettings(
            base_url=app.weknora_base_url,
            email=app.weknora_email,
            password=app.weknora_password,
            embedding_model=app.weknora_embedding_model,
            summary_model_id=app.weknora_summary_model_id,
            wiki_synthesis_model_id=app.weknora_wiki_synthesis_model_id,
        )
    )


async def main() -> None:
    engine = build()
    client = engine._client  # noqa: SLF001 - probe only, reads the raw payload
    bases = await client.get_data("/knowledge-bases")
    rows = bases.get("data") if isinstance(bases, dict) else bases
    if isinstance(rows, dict):
        rows = rows.get("items") or rows.get("list") or []
    print("bases:", len(rows))
    report = []
    for base in rows[:2]:
        base_id = str(base.get("id"))
        documents = await engine.list_documents(base_id)
        report.append((base_id, str(base.get("name")), len(documents)))
        print(f"base {base_id} ({base.get('name')}) documents={len(documents)}")
        if not documents:
            continue
        detail = await client.get_document(documents[0].document_id)
        payload = detail.get("data") if isinstance(detail, dict) and "data" in detail else detail
        fields = sorted(payload.keys()) if isinstance(payload, dict) else []
        returned = payload.get("knowledge_base_id") if isinstance(payload, dict) else None
        print("  document fields:", fields)
        print("  knowledge_base_id present:", "knowledge_base_id" in fields)
        print("  knowledge_base_id matches base:", str(returned) == base_id)
        print("  engine-mapped status:", documents[0].knowledge_base_id or "<empty>")
    print("summary:", json.dumps([(b, n, c) for b, n, c in report], ensure_ascii=False))


asyncio.run(main())
