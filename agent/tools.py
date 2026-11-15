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
CONTEXT_TOOL_PREFIX = "undercover-context___"
APP_OPERATIONS = {
    "get_claim",
    "get_policy",
    "get_claim_history",
    "calculate_payout",
    "record_decision",
    "pay_claim",
}
CONTEXT_OPERATIONS = {
    "lookup_term",
    "describe_fields",
    "get_plan",
    "get_claim_rules",
    "search_reference_data",
    "search_documents",
    "read_section",
}


class StrictToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyInput(StrictToolInput):
    pass


class QueryInput(StrictToolInput):
    query: str


class SectionInput(StrictToolInput):
    document: str
    number: int


class TermInput(StrictToolInput):
    term: str


class RecordInput(StrictToolInput):
    record: str


class PlanInput(StrictToolInput):
    plan: str


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
        if name in CONTEXT_OPERATIONS:
            return self.invoke_context(name, arguments)
        if not self.selected_gateway:
            return _invoke_direct(self.direct, self.claim_context, name, arguments)
        tool = self.remote.get(f"{APP_TOOL_PREFIX}{name}")
        if tool is None:
            raise RuntimeError(f"Gateway is missing app operation {name}")
        return _invoke_gateway(
            tool,
            self.claim_context,
            name,
            arguments,
        )

    def invoke_context(self, name: str, arguments: dict[str, Any]) -> Any:
        if not self.selected_gateway:
            try:
                return self.direct.invoke_context_tool(name, arguments)
            except ToolException as error:
                log_activity(self.claim_context.claim_id, f"Context refusal: {error}")
                raise
        tool = self.remote.get(f"{CONTEXT_TOOL_PREFIX}{name}")
        if tool is None:
            raise RuntimeError(f"Gateway is missing context operation {name}")
        return _invoke_gateway(tool, self.claim_context, name, arguments)


def _gateway_tools_by_operation() -> dict[str, Any]:
    return {
        tool.name: tool
        for tool in gateway_tools()
        if (
            tool.name.startswith(APP_TOOL_PREFIX)
            and tool.name.removeprefix(APP_TOOL_PREFIX) in APP_OPERATIONS
        )
        or (
            tool.name.startswith(CONTEXT_TOOL_PREFIX)
            and tool.name.removeprefix(CONTEXT_TOOL_PREFIX) in CONTEXT_OPERATIONS
        )
    }


def _arguments_for_gateway(
    operation: str, arguments: dict[str, Any], context: ClaimContext
) -> dict[str, Any]:
    ids = {
        "get_claim": {"claim_id": context.claim_id},
        "get_policy": {"policy_id": context.policy_id},
        "get_claim_history": {"customer_id": context.customer_id},
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
        gateway_arguments = (
            _arguments_for_gateway(operation, arguments, context)
            if operation in APP_OPERATIONS
            else arguments
        )
        result = invoke_gateway_tool(tool, gateway_arguments)
    except McpError as error:
        reason = str(error)
        log_activity(context.claim_id, f"Gateway refusal: {reason}")
        raise ToolException(reason) from error
    except ToolException as error:
        if operation in CONTEXT_OPERATIONS:
            log_activity(context.claim_id, f"Context refusal: {error}")
            raise
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

    def context_call(operation: str, **arguments: Any) -> Any:
        return call(operation, arguments)

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
        StructuredTool.from_function(
            name="lookup_term",
            description="Look up a defined term and its source.",
            func=lambda term: context_call("lookup_term", term=term),
            args_schema=TermInput,
        ),
        StructuredTool.from_function(
            name="describe_fields",
            description="Describe the fields in a business record.",
            func=lambda record: context_call("describe_fields", record=record),
            args_schema=RecordInput,
        ),
        StructuredTool.from_function(
            name="get_plan",
            description="Get a plan's benefits, deductibles and add-ons.",
            func=lambda plan: context_call("get_plan", plan=plan),
            args_schema=PlanInput,
        ),
        StructuredTool.from_function(
            name="get_claim_rules",
            description="Get the claims rules and their source.",
            func=lambda: context_call("get_claim_rules"),
            args_schema=EmptyInput,
        ),
        StructuredTool.from_function(
            name="search_reference_data",
            description="Search reference data for a query.",
            func=lambda query: context_call("search_reference_data", query=query),
            args_schema=QueryInput,
        ),
        StructuredTool.from_function(
            name="search_documents",
            description="Search both policy wording and claims manual documents.",
            func=lambda query: context_call("search_documents", query=query),
            args_schema=QueryInput,
        ),
        StructuredTool.from_function(
            name="read_section",
            description="Read a numbered policy wording or claims manual section.",
            func=lambda document, number: context_call(
                "read_section", document=document, number=number
            ),
            args_schema=SectionInput,
        ),
    ]


def canonical_security_claim(bundle: dict[str, Any]) -> str:
    """Serialize the same stable claim evidence exposed by get_claim."""
    return canonical_claim(bundle)
