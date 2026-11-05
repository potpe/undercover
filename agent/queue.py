"""The queue-organizing UnderCover Agent."""

from http import HTTPStatus
from typing import Any

from langchain.agents import create_agent
from langchain_core.tools import tool

from agent.main import backend, model


def queue_tools(run_id: int, model_name: str):
    @tool
    def list_claims() -> dict[str, Any]:
        """List waiting claims eligible for this run, including its cap and target."""
        with backend() as client:
            run_response = client.get(f"/api/runs/{run_id}")
            claims_response = client.get("/api/claims")
        run_response.raise_for_status()
        claims_response.raise_for_status()
        run = run_response.json()
        claims = claims_response.json()
        waiting = [claim for claim in claims if claim["status"] == "waiting"]
        if run["target_claim_id"]:
            waiting = [
                claim
                for claim in waiting
                if claim["claim_id"] == run["target_claim_id"]
            ]
        return {
            "claim_cap": run["claim_cap"],
            "target_claim_id": run["target_claim_id"],
            "claims": [
                {
                    "claim_id": claim["claim_id"],
                    "customer_name": claim["customer_name"],
                    "claim_type": claim["claim_type"],
                    "summary": claim["summary"],
                }
                for claim in waiting
            ],
        }

    @tool
    def plan_todos(claim_ids: list[str]) -> dict[str, Any]:
        """Save the agent's ordered to-do list within the run's claim cap."""
        with backend() as client:
            response = client.post(
                f"/api/runs/{run_id}/plan",
                json={"claim_ids": claim_ids, "model_name": model_name},
            )
        if response.status_code == HTTPStatus.CONFLICT:
            return response.json()
        response.raise_for_status()
        return response.json()

    @tool
    def work_claim(claim_id: str) -> dict[str, Any]:
        """Delegate one selected claim to a fresh subagent and return its outcome."""
        with backend() as client:
            response = client.post(
                f"/api/runs/{run_id}/work", json={"claim_id": claim_id}
            )
        response.raise_for_status()
        return response.json()

    @tool
    def check_todos() -> list[dict[str, Any]]:
        """Check the run's current to-do list for missed or incomplete claims."""
        with backend() as client:
            response = client.get(f"/api/runs/{run_id}")
        response.raise_for_status()
        return response.json()["todos"]

    @tool
    def finish_run(summary: str) -> dict[str, Any]:
        """Finish with a handler summary after every selected claim is addressed."""
        with backend() as client:
            response = client.post(
                f"/api/runs/{run_id}/finish", json={"summary": summary}
            )
        if response.status_code == HTTPStatus.CONFLICT:
            return response.json()
        response.raise_for_status()
        return response.json()

    return [list_claims, plan_todos, work_claim, check_todos, finish_run]


def run(run_id: int, thread_id: str) -> str:
    """Run the queue agent for one stored agent run."""
    with backend() as client:
        response = client.get("/api/agent/instructions")
    response.raise_for_status()
    selected_model = model()
    agent = create_agent(
        selected_model,
        tools=queue_tools(run_id, selected_model.model_name),
        system_prompt=response.json()["queue"],
    )
    result = agent.invoke(
        {"messages": [{"role": "user", "content": f"Work queue run {run_id}"}]},
        config={"configurable": {"thread_id": thread_id}},
    )
    return result["messages"][-1].text
