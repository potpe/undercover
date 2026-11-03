"""The UnderCover Agent."""

import os
from typing import Any, Literal

import httpx
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

load_dotenv(override=True)

BACKEND_URL = "http://localhost:8000"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-6-luna"


def backend() -> httpx.Client:
    """Return a client for the UnderCover backend."""
    return httpx.Client(base_url=BACKEND_URL, timeout=10)


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
def read_policy_wording(claim_id: str) -> dict[str, str]:
    """Read the policy wording and identify the claim being worked for activity."""
    with backend() as client:
        response = client.get("/api/policy-wording")
    response.raise_for_status()
    wording = response.json()
    log_activity(claim_id, f"Read policy wording {wording['version']}")
    return wording


@tool
def record_claim_decision(
    claim_id: str,
    decision: Literal["approve", "decline", "refer"],
    payout: float,
    reason: str,
    policy_section: str,
) -> str:
    """Record one claim decision, its USD payout, reason and policy section."""
    with backend() as client:
        response = client.post(
            f"/api/claims/{claim_id}/decision",
            json={
                "decision": decision,
                "payout": payout,
                "reason": reason,
                "policy_section": policy_section,
            },
        )
    response.raise_for_status()
    return f"Decision recorded. Claim status: {response.json()['claim']['status']}."


@tool
def pay_claim(claim_id: str, amount: float) -> str:
    """Record an approved claim's exact USD payment after backend rule checks."""
    with backend() as client:
        response = client.post(
            f"/api/claims/{claim_id}/payment", json={"amount": amount}
        )
    response.raise_for_status()
    return f"Payment recorded for ${amount:,.2f}."


def model() -> ChatOpenAI:
    """Return the model named in MODEL_NAME, or GPT-6 Luna by default."""
    name = os.getenv("MODEL_NAME")
    return ChatOpenAI(
        model=name or DEFAULT_MODEL,
        base_url=OPENROUTER_BASE_URL,
        api_key=SecretStr(os.environ["OPENROUTER_API_KEY"]),
        reasoning_effort=None if name else "low",
    )


def run(claim_id: str, thread_id: str) -> str:
    """Run the agent on one claim and return its final reply."""
    with backend() as client:
        response = client.get("/api/agent/instructions")
    response.raise_for_status()
    agent = create_agent(
        model(),
        tools=[read_claim, read_policy_wording, record_claim_decision, pay_claim],
        system_prompt=response.json()["content"],
    )
    result = agent.invoke(
        {"messages": [{"role": "user", "content": f"Work claim {claim_id}"}]},
        config={"configurable": {"thread_id": thread_id}},
    )
    return result["messages"][-1].text
