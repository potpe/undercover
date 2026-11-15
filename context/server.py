"""FastMCP server and AWS Lambda adapter for the context service."""

from typing import Any

from mangum import Mangum
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from context.documents import read_section, search_documents
from context.service import (
    describe_fields,
    get_claim_rules,
    get_plan,
    lookup_term,
    search_reference_data,
)


def build_server(*, lambda_transport: bool = False) -> FastMCP:
    """Build the stateless JSON server exposing seven read-only context tools."""
    options: dict[str, Any] = {}
    if lambda_transport:
        options["transport_security"] = TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        )
    server = FastMCP(
        "undercover-context",
        stateless_http=True,
        json_response=True,
        **options,
    )

    annotations = ToolAnnotations(readOnlyHint=True)
    server.tool(annotations=annotations)(lookup_term)
    server.tool(annotations=annotations)(describe_fields)
    server.tool(annotations=annotations)(get_plan)
    server.tool(annotations=annotations)(get_claim_rules)
    server.tool(annotations=annotations)(search_reference_data)
    server.tool(annotations=annotations)(search_documents)
    server.tool(annotations=annotations)(read_section)
    return server


server = build_server()
app = server.streamable_http_app()


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Serve one Function URL request through a fresh FastMCP lifespan."""
    app_for_invocation = build_server(lambda_transport=True).streamable_http_app()
    return Mangum(app_for_invocation, lifespan="auto")(event, context)
