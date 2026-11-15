def test_document_search_and_read_operations_are_context_owned(client):
    for path, payload in (
        (
            "/api/claims/CLM-1001/document-search",
            {"document": "claims_manual", "query": "meals without receipts"},
        ),
        (
            "/api/claims/CLM-1001/document-section",
            {"document": "claims_manual", "section_id": "10"},
        ),
    ):
        assert client.post(path, json=payload).status_code == 404
    schema = client.get("/openapi.json").json()
    assert "/api/claims/{claim_id}/document-search" not in schema["paths"]
    assert "/api/claims/{claim_id}/document-section" not in schema["paths"]


def test_instructions_contain_only_the_claim_agent_role(client):
    response = client.get("/api/agent/instructions")

    assert response.status_code == 200
    instructions = response.json()
    assert set(instructions) == {"claim"}
    assert "search_documents" in instructions["claim"]
    assert "read_section" in instructions["claim"]
    assert "record_decision" in instructions["claim"]
    assert "third claim" in instructions["claim"]
    assert "two or three sentences" in instructions["claim"]
