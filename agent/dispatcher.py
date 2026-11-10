"""A model-free LangGraph dispatcher for dispatches and evaluations."""

import operator
from dataclasses import dataclass
from typing import Annotated, Any, Literal, TypedDict, cast

from langchain_core.exceptions import ModelRateLimitError, ModelTimeoutError
from langgraph.errors import NodeError
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, RetryPolicy, Send

from agent.main import backend

RunKind = Literal["dispatch", "evaluation"]
WorkResult = dict[str, Any]


class WorkItem(TypedDict):
    claim_id: str
    run_id: int
    run_kind: RunKind


@dataclass(frozen=True)
class Harness:
    work: Any
    on_error: Any


class DispatchState(TypedDict):
    run_id: int
    run_kind: RunKind
    claims: list[WorkItem]
    results: Annotated[list[WorkResult], operator.add]


def settle_error(item: WorkItem, error: Exception, harness: Harness) -> Any:
    outcome = harness.on_error(item, error)
    if outcome["status"] == "retry":
        return Command(update={"results": [outcome]}, goto=Send("work_claim", item))
    return {"results": [outcome]}


def list_claims(state: DispatchState) -> dict[str, Any]:
    return {"claims": state["claims"]}


def fan_out(state: DispatchState) -> list[Send]:
    return [Send("work_claim", item) for item in state["claims"]]


def collect(state: DispatchState) -> dict[str, Any]:
    return {}


def build_dispatcher(harness: Harness):
    """Build the no-model fan-out graph around one claim-work harness."""

    def work_claim(state: WorkItem) -> Any:
        try:
            return {"results": [harness.work(state)]}
        except (ModelRateLimitError, ModelTimeoutError) as error:
            error.__dict__["claim_work_item"] = state
            raise
        except Exception as error:
            return settle_error(state, error, harness)

    def handle_error(state: DispatchState, error: NodeError) -> Any:
        failure = error.error
        if not isinstance(failure, Exception):
            raise failure
        item = getattr(failure, "claim_work_item", None)
        if item is None:
            raise failure
        return settle_error(item, failure, harness)

    builder = StateGraph(DispatchState)
    builder.add_node("list_claims", list_claims)
    builder.add_node(
        "work_claim",
        work_claim,
        retry_policy=RetryPolicy(
            max_attempts=3,
            retry_on=(ModelRateLimitError, ModelTimeoutError),
        ),
        error_handler=cast(Any, handle_error),
    )
    builder.add_node("collect", collect)
    builder.add_edge(START, "list_claims")
    builder.add_conditional_edges("list_claims", fan_out, ["work_claim"])
    builder.add_edge("work_claim", "collect")
    builder.add_edge("collect", END)
    return builder.compile()


def selected_work(run_id: int, run_kind: RunKind) -> list[WorkItem]:
    with backend() as client:
        response = client.get(f"/api/internal/runs/{run_id}")
    response.raise_for_status()
    return [
        {
            "claim_id": claim["claim_id"],
            "run_id": run_id,
            "run_kind": run_kind,
        }
        for claim in response.json()["claims"]
        if claim["status"] in ("waiting", "in_progress")
    ]


def run_dispatch(
    run_id: int,
    run_kind: RunKind,
    concurrency: int,
    harness: Harness,
    claim_ids: list[str] | None = None,
) -> DispatchState:
    """Fan out the selected claims with at most N active claim workers."""
    ids = (
        claim_ids
        if claim_ids is not None
        else [item["claim_id"] for item in selected_work(run_id, run_kind)]
    )
    claims: list[WorkItem] = [
        {"claim_id": claim_id, "run_id": run_id, "run_kind": run_kind}
        for claim_id in ids
    ]
    initial: DispatchState = {
        "run_id": run_id,
        "run_kind": run_kind,
        "claims": claims,
        "results": [],
    }
    graph = build_dispatcher(harness)
    return cast(
        DispatchState,
        graph.invoke(initial, {"max_concurrency": concurrency}),
    )
