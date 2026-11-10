def test_manual_search_and_section_read_are_scoped_and_recorded(client):
    search = client.post(
        "/api/claims/CLM-1001/document-search",
        json={"document": "claims_manual", "query": "meals without receipts"},
    )

    assert search.status_code == 200
    results = search.json()
    assert results[0]["section_id"] == "10"
    assert results[0]["title"] == "Meals without receipts"
    assert "full 24 hours" not in results[0]["snippet"]

    section = client.post(
        "/api/claims/CLM-1001/document-section",
        json={"document": "claims_manual", "section_id": "10"},
    )
    assert section.status_code == 200
    assert "$35 per traveler" in section.json()["content"]
    assert "# UnderCover Claims Handling Manual" not in section.json()["content"]

    client.post(
        "/api/claims/CLM-1001/document-section",
        json={"document": "claims_manual", "section_id": "10"},
    )
    bundle = client.get("/api/claims/CLM-1001").json()
    assert bundle["documents_read"] == [
        {
            "document": "claims_manual",
            "section_id": "10",
            "title": "Meals without receipts",
        }
    ]
    assert bundle["activity"][-1]["message"] == (
        "Read Claims manual section 10: Meals without receipts"
    )


def test_search_finds_manual_interpretation_and_history_rules(client):
    cases = {
        "wild animals": "7",
        "third claim in 12 months": "9",
        "unattended belongings": "7",
        "pre-existing conditions": "7",
    }
    for query, section_id in cases.items():
        response = client.post(
            "/api/claims/CLM-1001/document-search",
            json={"document": "claims_manual", "query": query},
        )
        assert section_id in [result["section_id"] for result in response.json()]


def test_policy_lookup_returns_sections_not_the_whole_document(client):
    search = client.post(
        "/api/claims/CLM-1001/document-search",
        json={"document": "policy_wording", "query": "trip delay waiting period"},
    )
    assert search.status_code == 200
    assert search.json()[0]["section_id"] == "3"

    section = client.post(
        "/api/claims/CLM-1001/document-section",
        json={"document": "policy_wording", "section_id": "3"},
    )
    assert "Trip Delay" in section.json()["title"]
    assert "mechanical or crew problem" in section.json()["content"]
    assert (
        "# UnderCover Travel Insurance: Policy Wording" not in section.json()["content"]
    )


def test_document_operations_reject_bad_input_and_unknown_claims(client):
    unknown_document = client.post(
        "/api/claims/CLM-1001/document-search",
        json={"document": "handlers_decisions", "query": "claim"},
    )
    assert unknown_document.status_code == 422
    unknown_section = client.post(
        "/api/claims/CLM-1001/document-section",
        json={"document": "claims_manual", "section_id": "99"},
    )
    assert unknown_section.status_code == 404
    unknown_claim = client.post(
        "/api/claims/CLM-9999/document-search",
        json={"document": "claims_manual", "query": "claim"},
    )
    assert unknown_claim.status_code == 404


def test_instructions_contain_only_the_claim_agent_role(client):
    response = client.get("/api/agent/instructions")

    assert response.status_code == 200
    instructions = response.json()
    assert set(instructions) == {"claim"}
    assert "policy wording" in instructions["claim"]
    assert "claims manual" in instructions["claim"]
    assert "third claim" in instructions["claim"]
    assert "two or three sentences" in instructions["claim"]
