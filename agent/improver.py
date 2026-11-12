"""A recipe-guided improver with durable, handler-approved proposal tools."""

import json
import os
from typing import Any
from uuid import uuid4

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    HumanInTheLoopMiddleware,
    ModelCallLimitMiddleware,
)
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from agent import improver_api as api

MAX_MODEL_CALLS = 30
GOALS = """You improve UnderCover's claim agent configuration.
Your goal includes preserving its business rules.
The claim agent works from its instructions, policy wording and claims manual,
the claim and documents, the policy, and the customer's earlier claims.
It has tools for searching and reading document sections and calculating payouts.
Its graph records decisions, waits for sign-off and pays approved claims.
Your changes express general handling rules and never name an individual claim.
Business objectives, noise, tolerance, spending, allowed models, evaluation sets,
the judge and the approval limit are fixed controls, not yours to change.
Only approved proposals take effect. A refusal is feedback for a better proposal.
Held-out information is aggregate scores only. Your recipe is the data below.
Your goal is one approved candidate, its tested verdict, and a better approved recipe.
"""


class UsageMiddleware(AgentMiddleware):
    def __init__(self, round_id: int):
        self.round_id = round_id

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        api.check_budget(self.round_id)
        response = handler(request)
        for message in response.result:
            usage = message.response_metadata.get("token_usage")
            if usage:
                api.record_usage(
                    self.round_id,
                    {
                        "call_id": message.id or str(uuid4()),
                        "model_name": request.model.model_name,
                        "reasoning_effort": request.model.reasoning_effort or "default",
                        "prompt_tokens": usage["prompt_tokens"],
                        "completion_tokens": usage["completion_tokens"],
                        "total_tokens": usage["total_tokens"],
                        "cost": usage["cost"],
                    },
                )
        return response


def make_model(record: dict) -> ChatOpenAI:
    catalog = record["settings"]["models_allowed"]
    free = catalog[0]["reasoning_efforts"] == ["default"]
    settings: dict[str, Any] = {
        "model": catalog[0]["model_name"] if free else "openai/gpt-6-luna",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": SecretStr(os.environ["OPENROUTER_API_KEY"]),
        "timeout": 300,
        "max_retries": 5,
        "model_kwargs": {"parallel_tool_calls": False},
    }
    if not free:
        settings["reasoning_effort"] = "medium"
    return ChatOpenAI(**settings)


def make_tools(round_id: int, evaluate: Any) -> list:
    @tool
    def read_results() -> str:
        """Read configuration, scores, evaluation mistakes, settings and budget."""
        return json.dumps(api.read_results(round_id))

    @tool
    def propose_change(
        instructions: str, model: str, reasoning_effort: str, reason: str
    ) -> str:
        """Apply the complete configuration proposal only after handler approval."""
        record = api.apply_proposal(round_id, "configuration")
        return f"Saved version {record['candidate_version']}. Run the evaluation next."

    @tool
    def run_evaluation() -> str:
        """Evaluate the approved candidate once on evaluation and twice on held-out."""
        return json.dumps(evaluate(round_id))

    @tool
    def propose_recipe(recipe: str, reason: str) -> str:
        """Apply the complete recipe proposal only after handler approval."""
        api.apply_proposal(round_id, "recipe")
        return "Saved the approved recipe version."

    return [read_results, propose_change, run_evaluation, propose_recipe]


def make_improver(record: dict, recipe: str, checkpointer: Any, evaluate: Any):
    middleware: list[Any] = [
        UsageMiddleware(record["round_id"]),
        ModelCallLimitMiddleware(thread_limit=MAX_MODEL_CALLS, exit_behavior="end"),
        HumanInTheLoopMiddleware(
            interrupt_on={
                "propose_change": {"allowed_decisions": ["approve", "reject"]},
                "propose_recipe": {"allowed_decisions": ["approve", "reject"]},
            }
        ),
    ]
    return create_agent(
        model=make_model(record),
        tools=make_tools(record["round_id"], evaluate),
        system_prompt=f"{GOALS}\n# Your recipe\n{recipe}",
        middleware=middleware,
        checkpointer=checkpointer,
    )
