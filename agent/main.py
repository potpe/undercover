"""The durable UnderCover claim graph and its backend tools."""

import os
from datetime import UTC, datetime
from time import perf_counter
from typing import Annotated, Any, Literal, TypedDict, cast

import httpx
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, interrupt
from pydantic import SecretStr

from agent.checkpoints import claim_checkpointer
from agent.tracing import trace_claim
from backend.models import (
    Benefit,
    DecisionKind,
    DecisionRequest,
    ModelCallRequest,
    PayoutLine,
    WorkPeriodRequest,
)

BACKEND_URL = "http://localhost:8000"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-6-luna"
MAX_MODEL_CALLS = 30


class ClaimState(TypedDict):
    """The durable state of one claim graph."""

    messages: Annotated[list[AnyMessage], add_messages]
    claim_id: str
    model_calls: int
    decision: DecisionKind | None
    payout: float
    reason: str
    policy_section: str
    claim_status: str
    approved_by: str | None
    result: str
    run_id: int | None
    run_kind: str


def backend() -> httpx.Client:
    """Return a client for the UnderCover backend."""
    return httpx.Client(base_url=BACKEND_URL, timeout=120)


def log_activity(claim_id: str, message: str) -> None:
    with backend() as client:
        response = client.post(
            f"/api/claims/{claim_id}/activity", json={"message": message}
        )
    response.raise_for_status()


@tool
def read_claim(claim_id: str) -> dict[str, Any]:
    """Read a claim, its policy and plan, and the customer's claim history."""
    with backend() as client:
        response = client.get(f"/api/claims/{claim_id}")
    response.raise_for_status()
    log_activity(claim_id, "Read claim, policy and customer claim history")
    return response.json()


@tool
def search_documents(
    claim_id: str, document: Literal["policy_wording", "claims_manual"], query: str
) -> list[dict[str, Any]]:
    """Find relevant policy wording or claims manual sections for a claim."""
    with backend() as client:
        response = client.post(
            f"/api/claims/{claim_id}/document-search",
            json={"document": document, "query": query},
        )
    response.raise_for_status()
    return response.json()


@tool
def read_document_section(
    claim_id: str, document: Literal["policy_wording", "claims_manual"], section_id: str
) -> dict[str, str]:
    """Read one policy wording or claims manual section for a claim."""
    with backend() as client:
        response = client.post(
            f"/api/claims/{claim_id}/document-section",
            json={"document": document, "section_id": section_id},
        )
    response.raise_for_status()
    return response.json()


@tool(parse_docstring=True)
def calculate_payout(
    claim_id: str,
    benefit: Benefit,
    lines: list[PayoutLine],
    paid_by_others: float,
) -> dict[str, Any]:
    """Work out an approved claim's payout with UnderCover's backend rules.

    Args:
        claim_id: The claim being worked
        benefit: The benefit the claim is paid under
        lines: Every covered item or expense, excluding ineligible lines.
            Use zero for nights or hours that do not apply.
        paid_by_others: Total paid or refunded by carriers, hotels or others

    Returns:
        The calculation, exact payout and whether the backend requires sign-off
    """
    with backend() as client:
        response = client.post(
            f"/api/claims/{claim_id}/payout",
            json={
                "benefit": benefit,
                "lines": [line.model_dump() for line in lines],
                "paid_by_others": paid_by_others,
            },
        )
    response.raise_for_status()
    return response.json()


def save_decision(claim_id: str, request: DecisionRequest) -> dict[str, Any]:
    with backend() as client:
        response = client.post(
            f"/api/claims/{claim_id}/decision", json=request.model_dump()
        )
    response.raise_for_status()
    return response.json()


def save_payment(claim_id: str, amount: float) -> dict[str, Any]:
    with backend() as client:
        response = client.post(
            f"/api/claims/{claim_id}/payment", json={"amount": amount}
        )
    response.raise_for_status()
    return response.json()


def utcnow() -> datetime:
    return datetime.now(UTC)


def save_model_usage(state: ClaimState, selected_model: Any, response: Any) -> None:
    usage = response.response_metadata.get("token_usage")
    if not usage:
        return
    request = ModelCallRequest(
        run_id=state["run_id"],
        model_name=selected_model.model_name,
        reasoning_effort=selected_model.reasoning_effort,
        prompt_tokens=usage["prompt_tokens"],
        completion_tokens=usage["completion_tokens"],
        total_tokens=usage["total_tokens"],
        cost=usage["cost"],
    )
    with backend() as client:
        result = client.post(
            f"/api/claims/{state['claim_id']}/model-calls",
            json=request.model_dump(),
        )
    result.raise_for_status()


def save_work_period(
    claim_id: str,
    thread_id: str,
    run_id: int | None,
    started_at: datetime,
    started: float,
) -> None:
    request = WorkPeriodRequest(
        run_id=run_id,
        thread_id=thread_id,
        started_at=started_at,
        finished_at=utcnow(),
        seconds=perf_counter() - started,
    )
    with backend() as client:
        response = client.post(
            f"/api/claims/{claim_id}/work-periods",
            json=request.model_dump(mode="json"),
        )
    response.raise_for_status()


def claim_config(
    thread_id: str,
    callbacks: list[Any],
    run_id: int | None,
    run_kind: str,
) -> RunnableConfig:
    tags = [f"{run_kind}-{run_id}"] if run_id is not None else []
    return {
        "configurable": {"thread_id": thread_id},
        "callbacks": callbacks,
        "metadata": {"langfuse_tags": tags} if tags else {},
    }


def model() -> ChatOpenAI:
    """Return the model named in MODEL_NAME, or GPT-6 Luna by default."""
    name = os.getenv("MODEL_NAME")
    return ChatOpenAI(
        model=name or DEFAULT_MODEL,
        base_url=OPENROUTER_BASE_URL,
        api_key=SecretStr(os.environ["OPENROUTER_API_KEY"]),
        reasoning_effort=None if name else "low",
        timeout=60,
        max_completion_tokens=8192,
    )


def route_after_agent(state: ClaimState) -> str:
    if getattr(state["messages"][-1], "tool_calls", []):
        return "tools"
    if state.get("model_calls", 0) >= MAX_MODEL_CALLS:
        return END
    return "decide"


def route_after_tools(state: ClaimState) -> str:
    return END if state.get("model_calls", 0) >= MAX_MODEL_CALLS else "agent"


def route_after_decision(state: ClaimState) -> str:
    if state["decision"] != "approve":
        return END
    if state["claim_status"] == "waiting_for_sign_off":
        return "approval"
    if state["claim_status"] == "in_progress":
        return "pay"
    return END


def build_claim_graph(
    selected_model: Any, instructions: str, checkpointer: BaseCheckpointSaver
):
    system_message = SystemMessage(instructions)
    model_with_tools = selected_model.bind_tools(
        [read_claim, search_documents, read_document_section, calculate_payout]
    )
    decider = selected_model.with_structured_output(DecisionRequest, include_raw=True)
    tools = ToolNode(
        [read_claim, search_documents, read_document_section, calculate_payout]
    )

    def agent(state: ClaimState) -> dict[str, Any]:
        response = model_with_tools.invoke([system_message, *state["messages"]])
        save_model_usage(state, selected_model, response)
        return {
            "messages": [response],
            "model_calls": state.get("model_calls", 0) + 1,
        }

    def decide(state: ClaimState) -> dict[str, Any]:
        structured = decider.invoke(
            [
                system_message,
                *state["messages"],
                HumanMessage("Record your decision on this claim."),
            ]
        )
        save_model_usage(state, selected_model, structured["raw"])
        result = structured["parsed"]
        log_activity(
            state["claim_id"],
            f"Decision prepared: {result.decision} for ${result.payout:,.2f}",
        )
        return {
            "decision": result.decision,
            "payout": result.payout,
            "reason": result.reason,
            "policy_section": result.policy_section,
            "model_calls": state.get("model_calls", 0) + 1,
        }

    def record_decision(state: ClaimState) -> dict[str, Any]:
        bundle = save_decision(
            state["claim_id"],
            DecisionRequest(
                decision=cast(DecisionKind, state["decision"]),
                payout=state["payout"],
                reason=state["reason"],
                policy_section=state["policy_section"],
            ),
        )
        status = bundle["claim"]["status"]
        result = f"{state['claim_id']}: {state['decision']} ${state['payout']:,.2f}"
        if status == "waiting_for_sign_off":
            result += "; waiting for sign-off"
        return {"claim_status": status, "result": result}

    def approval(state: ClaimState) -> dict[str, Any]:
        answer = interrupt({"claim_id": state["claim_id"], "payout": state["payout"]})
        return {"approved_by": answer["handler_id"], "claim_status": "in_progress"}

    def pay(state: ClaimState) -> dict[str, Any]:
        save_payment(state["claim_id"], state["payout"])
        return {
            "claim_status": "decided",
            "result": (
                f"{state['claim_id']}: approve ${state['payout']:,.2f}; "
                "payment recorded"
            ),
        }

    builder = StateGraph(ClaimState)
    builder.add_node("agent", agent)
    builder.add_node("tools", tools)
    builder.add_node("decide", decide)
    builder.add_node("record_decision", record_decision)
    builder.add_node("approval", approval)
    builder.add_node("pay", pay)
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", route_after_agent, ["tools", "decide", END])
    builder.add_conditional_edges("tools", route_after_tools, ["agent", END])
    builder.add_edge("decide", "record_decision")
    builder.add_conditional_edges(
        "record_decision", route_after_decision, ["approval", "pay", END]
    )
    builder.add_edge("approval", "pay")
    builder.add_edge("pay", END)
    return builder.compile(checkpointer=checkpointer)


def current_bundle(claim_id: str) -> dict[str, Any]:
    with backend() as client:
        response = client.get(f"/api/claims/{claim_id}")
    response.raise_for_status()
    return response.json()


def run(claim_id: str, thread_id: str) -> str:
    """Start or continue one claim without a dispatch or evaluation."""
    return run_with_context(claim_id, thread_id, None, "dispatch")


def run_with_context(
    claim_id: str,
    thread_id: str,
    run_id: int | None = None,
    run_kind: str = "dispatch",
) -> str:
    """Start or continue one claim on its durable thread."""
    with backend() as client:
        response = client.get("/api/agent/instructions")
    response.raise_for_status()
    if run_id is None:
        run_id = current_bundle(claim_id).get("work_run_id")
    selected_model = model()
    with claim_checkpointer() as checkpointer:
        graph = build_claim_graph(
            selected_model, response.json()["claim"], checkpointer
        )
        config = claim_config(thread_id, [], run_id, run_kind)
        saved = graph.get_state(config)
        if saved.created_at is not None and not saved.next:
            return saved.values["result"]

        graph_input: ClaimState | Command[Any] | None
        if saved.interrupts:
            bundle = current_bundle(claim_id)
            if not bundle["signed_off_by"]:
                return saved.values["result"]
            graph_input = Command(resume={"handler_id": bundle["signed_off_by"]})
        elif saved.next:
            graph_input = None
        else:
            graph_input = {
                "messages": [HumanMessage(f"Work claim {claim_id}")],
                "claim_id": claim_id,
                "model_calls": 0,
                "decision": None,
                "payout": 0.0,
                "reason": "",
                "policy_section": "",
                "claim_status": "in_progress",
                "approved_by": None,
                "result": "",
                "run_id": run_id,
                "run_kind": run_kind,
            }
        started_at = utcnow()
        started = perf_counter()
        try:
            log_activity(claim_id, f"Using model {selected_model.model_name}")
            with trace_claim(claim_id, thread_id) as callbacks:
                config["callbacks"] = callbacks
                result = graph.invoke(graph_input, config, durability="sync")
                if "__interrupt__" in result:
                    bundle = current_bundle(claim_id)
                    if bundle["signed_off_by"]:
                        result = graph.invoke(
                            Command(resume={"handler_id": bundle["signed_off_by"]}),
                            config,
                            durability="sync",
                        )
        finally:
            save_work_period(claim_id, thread_id, run_id, started_at, started)
        if "result" in result:
            return result["result"]
        return result["messages"][-1].text
