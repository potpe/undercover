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
                assert set(content.get("required", [])) == set(content["properties"])

    assert len(operation_ids) == len(set(operation_ids))
