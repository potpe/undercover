AGENT_OPERATION_IDS = {
    "get_claim",
    "get_policy",
    "get_claim_history",
    "calculate_payout",
    "record_decision",
    "pay_claim",
}


def test_operations_have_ids_docstrings_and_required_inputs(client):
    schema = client.get("/openapi.json").json()
    operation_ids = []

    for path_item in schema["paths"].values():
        for operation in path_item.values():
            operation_ids.append(operation["operationId"])
            assert operation["description"]
            body = operation.get("requestBody")
            if body:
                assert body["required"] is True
                content = body["content"]["application/json"]["schema"]
                assert_required_inputs(content, schema["components"]["schemas"])

    assert len(operation_ids) == len(set(operation_ids))


def assert_required_inputs(content, components):
    if "$ref" in content:
        model_name = content["$ref"].rsplit("/", 1)[-1]
        assert_required_inputs(components[model_name], components)
    elif "oneOf" in content:
        for variant in content["oneOf"]:
            assert_required_inputs(variant, components)
    elif "anyOf" in content:
        for variant in content["anyOf"]:
            assert_required_inputs(variant, components)
    elif "properties" in content:
        assert set(content.get("required", [])) == set(content["properties"])


def test_agent_openapi_is_generated_filtered_and_credential_free(client):
    root_schema = client.get("/openapi.json").json()
    response = client.get("/api/agent/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    operations = [
        operation
        for path_item in schema["paths"].values()
        for operation in path_item.values()
    ]
    assert {operation["operationId"] for operation in operations} == AGENT_OPERATION_IDS
    assert len(operations) == len(AGENT_OPERATION_IDS)
    assert "servers" not in schema
    assert "/api/claims/{claim_id}/steps" not in schema["paths"]
    assert client.get("/openapi.json").json() == root_schema
    for operation in operations:
        assert operation["description"]
        assert not any(
            parameter["name"].lower() == "x-api-key"
            for parameter in operation.get("parameters", [])
        )
    payment = next(op for op in operations if op["operationId"] == "pay_claim")
    payment_schema = payment["requestBody"]["content"]["application/json"]["schema"]
    payment_schema = schema["components"]["schemas"][
        payment_schema["$ref"].rsplit("/", 1)[-1]
    ]
    assert payment_schema["required"] == ["amount"]
    amount = payment_schema["properties"]["amount"]
    assert amount["type"] == "number"
    assert "/api/internal/claims/{claim_id}/gateway-hold" in root_schema["paths"]
    assert all(
        path != "/api/internal/claims/{claim_id}/gateway-hold"
        for path in schema["paths"]
    )


def test_agent_openapi_requires_agent_key_or_handler(client):
    client.headers.pop("Authorization")
    assert client.get("/api/agent/openapi.json").status_code == 200
    client.headers.pop("X-API-Key")
    client.headers["Authorization"] = "Bearer dana-token"
    assert client.get("/api/agent/openapi.json").status_code == 200
    client.headers.pop("Authorization")
    assert client.get("/api/agent/openapi.json").status_code == 401


def test_agent_read_views_match_claim_bundle_records(client):
    bundle = client.get("/api/claims/CLM-1001").json()
    policy = client.get(f"/api/policies/{bundle['policy']['policy_id']}").json()
    history = client.get(
        f"/api/customers/{bundle['customer']['customer_id']}/history"
    ).json()
    assert policy == bundle["policy"]
    assert history == bundle["customer"]["previous_claims"]
    assert "handler_decision" not in policy
    assert client.get("/api/policies/POL-missing").status_code == 404
    assert client.get("/api/customers/CUS-missing/history").status_code == 404
