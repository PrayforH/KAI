"""Live check of the document ownership rule against the 174 deployment.

Runs inside the API container and calls the same service the HTTP handler calls, on
the same database and the same WeKnora engine. The engine addresses documents by a
globally unique id, so before the ownership check, source A could serve source B's
document by id. Read-only: nothing is modified or deleted.
"""

import asyncio
import json

from harness.composition import build_production_container
from harness.config import Settings
from harness.core.errors import NotFoundError

TENANT = "local"
USER = "user_1c16a8994ff548c298c356fd8385eb76"
SOURCE_A = "aipolicy"
SOURCE_B = "overseas"


async def main() -> int:
    container = build_production_container(Settings())
    knowledge = container.knowledge

    documents_a = await knowledge.list_source_documents(TENANT, USER, SOURCE_A)
    documents_b = await knowledge.list_source_documents(TENANT, USER, SOURCE_B)
    own, foreign = documents_a[0], documents_b[0]

    report: dict[str, object] = {
        "sourceA": SOURCE_A,
        "sourceB": SOURCE_B,
        "ownDocument": own.document_id,
        "foreignDocument": foreign.document_id,
    }

    own_read = await knowledge.get_source_document(TENANT, USER, SOURCE_A, own.document_id)
    report["ownReads"] = own_read.document_id == own.document_id

    for label, call in (
        ("foreignDetail", knowledge.get_source_document(TENANT, USER, SOURCE_A, foreign.document_id)),
        ("foreignChunks", knowledge.list_source_chunks(TENANT, USER, SOURCE_A, foreign.document_id)),
    ):
        try:
            await call
            report[label] = "ALLOWED"
        except NotFoundError:
            report[label] = "refused"
        except Exception as error:  # noqa: BLE001 - report the class, keep verifying
            report[label] = f"error:{type(error).__name__}"

    via_own_source = await knowledge.get_source_document(
        TENANT, USER, SOURCE_B, foreign.document_id
    )
    report["foreignReadsViaItsOwnSource"] = via_own_source.document_id == foreign.document_id

    report["verdict"] = (
        "PASS"
        if report["ownReads"] is True
        and report["foreignDetail"] == "refused"
        and report["foreignChunks"] == "refused"
        and report["foreignReadsViaItsOwnSource"] is True
        else "FAIL"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["verdict"] == "PASS" else 1


raise SystemExit(asyncio.run(main()))
