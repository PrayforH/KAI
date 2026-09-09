from harness.knowledge.answer import knowledge_answer_payload, wiki_answer_payload
from harness.knowledge.models import KnowledgeWikiPage


def test_wiki_payload_identifies_base_and_reports_content_truncation() -> None:
    page = KnowledgeWikiPage(
        knowledgeBaseReference="cases",
        slug="entity/a",
        title="甲",
        pageType="entity",
        content="a" * 9000,
    )
    payload = wiki_answer_payload([page])
    result = payload["pages"][0]
    assert result["citationLink"] == "[[cases::entity/a|甲]]"
    assert result["knowledgeBaseReference"] == "cases"
    assert result["truncated"] is True
    assert len(result["content"]) == 8000


def test_rag_payload_uses_stable_source_and_chunk_link() -> None:
    from harness.knowledge.models import SearchKnowledgeResponse

    result = SearchKnowledgeResponse.model_validate(
        {
            "hits": [
                {
                    "matchedTerms": [],
                    "content": "证据",
                    "score": 1,
                    "trust": "untrusted",
                    "citation": {
                        "knowledgeBaseReference": "cases",
                        "sourceReference": "cases",
                        "sourceDisplayName": "案例",
                        "snapshotId": "s",
                        "documentId": "d",
                        "chunkId": "a/b",
                        "title": "资料[1]",
                        "uri": "https://example.com",
                    },
                }
            ],
            "searchedSnapshotIds": [],
        }
    )
    hit = knowledge_answer_payload(result)["hits"][0]
    assert hit["citationLink"] == "[资料（1）](citation:cases:a%2Fb)"
