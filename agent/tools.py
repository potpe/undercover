"""Claim-scoped model tools shared by the direct and Gateway routes."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.tools import BaseTool, StructuredTool, ToolException
from mcp.shared.exceptions import McpError
from pydantic import BaseModel, ConfigDict, Field

from agent.api import canonical_claim, log_activity
from agent.claim_context import ClaimContext
from agent.direct import DirectRoute
from agent.gateway import (
    gateway_tools,
    invoke_gateway_tool,
    is_gateway_tool_refusal,
)
from backend.models import Benefit, PayoutLine

APP_TOOL_PREFIX = "undercover-app___"
APP_OPERATIONS = {
    "get_claim",
    "get_policy",
    "get_claim_history",
    "search_documents",
    "read_section",
    "calculate_payout",
    "record_decision",
    "pay_claim",
}


class StrictToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyInput(StrictToolInput):
    pass


class SearchInput(StrictToolInput):
    document: Literal["policy_wording", "claims_manual"]
    query: str


class SectionInput(StrictToolInput):
    document: Literal["policy_wording", "claims_manual"]
    section_id: str


class StrictPayoutLine(PayoutLine):
    model_config = ConfigDict(extra="forbid")


class PayoutInput(StrictToolInput):
    benefit: Benefit
    lines: list[StrictPayoutLine]
    paid_by_others: float = Field(ge=0)


class DecisionInput(StrictToolInput):
    decision: Literal["approve", "decline", "refer"]
    payout: float = Field(ge=0)
    reason: str
    policy_section: str


@dataclass(frozen=True, slots=True)
class ModelToolRoute:
    claim_context: ClaimContext
    direct: DirectRoute
    selected_gateway: bool
    remote: dict[str, Any]

    def invoke(self, name: str, arguments: dict[str, Any]) -> Any:
        if not self.selected_gateway:
            return _invoke_direct(self.direct, self.claim_context, name, arguments)
        tool = self.remote.get(name)
        if tool is None:
            raise RuntimeError(f"Gateway is missing app operation {name}")
        return _invoke_gateway(
            tool,
            self.claim_context,
            name,
            arguments,
        )


def _gateway_tools_by_operation() -> dict[str, Any]:
    return {
        tool.name.removeprefix(APP_TOOL_PREFIX): tool
        for tool in gateway_tools()
        if tool.name.startswith(APP_TOOL_PREFIX)
        and tool.name.removeprefix(APP_TOOL_PREFIX) in APP_OPERATIONS
    }


def _arguments_for_gateway(
    operation: str, arguments: dict[str, Any], context: ClaimContext
) -> dict[str, Any]:
    ids = {
        "get_claim": {"claim_id": context.claim_id},
        "get_policy": {"policy_id": context.policy_id},
        "get_claim_history": {"customer_id": context.customer_id},
        "search_documents": {"claim_id": context.claim_id},
        "read_section": {"claim_id": context.claim_id},
        "calculate_payout": {"claim_id": context.claim_id},
        "record_decision": {
            "claim_id": context.claim_id,
            "decided_by": context.decision_maker,
        },
        "pay_claim": {"claim_id": context.claim_id},
    }
    return {**arguments, **ids[operation]}


def _invoke_gateway(
    tool: Any,
    context: ClaimContext,
    operation: str,
    arguments: dict[str, Any],
) -> Any:
    try:
        result = invoke_gateway_tool(
            tool, _arguments_for_gateway(operation, arguments, context)
        )
    except McpError as error:
        reason = str(error)
        log_activity(context.claim_id, f"Gateway refusal: {reason}")
        raise ToolException(reason) from error
    except ToolException as error:
        if not is_gateway_tool_refusal(error):
            raise
        reason = str(error)
        log_activity(context.claim_id, f"App refusal: {reason}")
        raise
    if operation == "get_claim":
        log_activity(context.claim_id, "Read claim, policy and customer claim history")
        if isinstance(result, dict):
            return canonical_claim(result)
        if isinstance(result, str):
            try:
                return canonical_claim(json.loads(result))
            except json.JSONDecodeError:
                return result
    return result


def _invoke_direct(
    direct: DirectRoute,
    context: ClaimContext,
    operation: str,
    arguments: dict[str, Any],
) -> Any:
    try:
        if operation == "record_decision":
            return direct.record_decision(
                {**arguments, "decided_by": context.decision_maker}
            )
        if operation == "calculate_payout":
            payload = {
                **arguments,
                "lines": [
                    line.model_dump() if isinstance(line, PayoutLine) else line
                    for line in arguments["lines"]
                ],
            }
            return direct.calculate_payout(**payload)
        return getattr(direct, operation)(**arguments)
    except Exception as error:
        response = getattr(error, "response", None)
        if response is None or not response.is_error:
            raise
        reason = response.text or str(error)
        log_activity(context.claim_id, f"App refusal: {reason}")
        raise ToolException(reason) from error


def route_tools(
    claim_context: ClaimContext,
    selected: str = "direct",
    on_decision: Callable[[dict[str, Any], dict[str, Any]], None] | None = None,
) -> list[BaseTool]:
    """Build the same identifier-free tool surface for one claim."""
    route = ModelToolRoute(
        claim_context=claim_context,
        direct=DirectRoute(claim_context),
        selected_gateway=selected == "gateway",
        remote=_gateway_tools_by_operation() if selected == "gateway" else {},
    )

    def call(name: str, arguments: dict[str, Any]) -> Any:
        return route.invoke(name, arguments)

    def record_decision(
        decision: Literal["approve", "decline", "refer"],
        payout: float,
        reason: str,
        policy_section: str,
    ) -> str:
        """Submit this claim's decision draft for backend validation."""
        payload = {
            "decision": decision,
            "payout": payout,
            "reason": reason,
            "policy_section": policy_section,
        }
        result = call("record_decision", payload)
        if on_decision:
            on_decision(payload, result)
        return json.dumps(result, ensure_ascii=False)

    def calculate_payout(
        benefit: Benefit, lines: list[StrictPayoutLine], paid_by_others: float
    ) -> Any:
        return call(
            "calculate_payout",
            {
                "benefit": benefit,
                "lines": [
                    line.model_dump() if isinstance(line, PayoutLine) else line
                    for line in lines
                ],
                "paid_by_others": paid_by_others,
            },
        )

    return [
        StructuredTool.from_function(
            name="get_claim",
            description="Read the complete claim bundle for the claim being handled.",
            func=lambda: call("get_claim", {}),
            args_schema=EmptyInput,
        ),
        StructuredTool.from_function(
            name="get_policy",
            description="Read the policy for the claim being handled.",
            func=lambda: call("get_policy", {}),
            args_schema=EmptyInput,
        ),
        StructuredTool.from_function(
            name="get_claim_history",
            description="Read the customer's claim history.",
            func=lambda: call("get_claim_history", {}),
            args_schema=EmptyInput,
        ),
        StructuredTool.from_function(
            name="search_documents",
            description="Find relevant policy wording or claims manual sections.",
            func=lambda document, query: call(
                "search_documents", {"document": document, "query": query}
            ),
            args_schema=SearchInput,
        ),
        StructuredTool.from_function(
            name="read_section",
            description="Read a policy wording or claims manual section.",
            func=lambda document, section_id: call(
                "read_section", {"document": document, "section_id": section_id}
            ),
            args_schema=SectionInput,
        ),
        StructuredTool.from_function(
            name="calculate_payout",
            description="Calculate a claim payout using backend rules.",
            func=calculate_payout,
            args_schema=PayoutInput,
        ),
        StructuredTool.from_function(
            name="record_decision",
            description="Submit the proposed decision for this claim.",
            func=record_decision,
            args_schema=DecisionInput,
        ),
    ]


def canonical_security_claim(bundle: dict[str, Any]) -> str:
    """Serialize the same stable claim evidence exposed by get_claim."""
    return canonical_claim(bundle)
