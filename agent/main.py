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
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"


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
    """Return the model named in MODEL_NAME, or DeepSeek V4.1 Flash by default."""
    name = os.getenv("MODEL_NAME")
    return ChatOpenAI(
        model=name or DEFAULT_MODEL,
        base_url=OPENROUTER_BASE_URL,
        api_key=SecretStr(os.environ["OPENROUTER_API_KEY"]),
        reasoning_effort=None if name else "low",
        timeout=60,
    )


def run(claim_id: str, thread_id: str) -> str:
    """Run the agent on one claim and return its final reply."""
    with backend() as client:
        response = client.get("/api/agent/instructions")
    response.raise_for_status()
    selected_model = model()
    log_activity(claim_id, f"Using model {selected_model.model_name}")
    agent = create_agent(
        selected_model,
        tools=[
            read_claim,
            search_documents,
            read_document_section,
            record_claim_decision,
            pay_claim,
        ],
        system_prompt=response.json()["claim"],
    )
    result = agent.invoke(
        {"messages": [{"role": "user", "content": f"Work claim {claim_id}"}]},
        config={"configurable": {"thread_id": thread_id}},
    )
    return result["messages"][-1].text
