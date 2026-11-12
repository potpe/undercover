"""The single OpenRouter Decisions boundary for backend-only rationale grading."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Any

from openrouter import OpenRouter
from openrouter.utils import BackoffStrategy, RetryConfig
from sqlalchemy import insert, select

from backend.database import (
    claims_table,
    connect,
    handler_decisions_table,
    judge_grades_table,
    run_claims_table,
    runs_table,
)
from backend.improvement_budget import check_run, check_run_connection

JUDGE_ORIGIN = os.getenv("JUDGE_ORIGIN", "https://openrouter.ai").rstrip("/")
GRADES = ("F", "C", "B", "A")
RUBRIC = (
    "F: different grounds from the handler's reason, "
    "or it contradicts the handler's reason",
    "C: partly the same as the handler's reason: one of the handler's grounds "
    "or the section is missing, or it adds a ground the handler did not give",
    "B: the same grounds and the same section of the policy wording "
    "as the handler's reason",
    "A: the same grounds and section as the handler's reason, "
    "and clearly better expressed: clearer for the customer, "
    "with the section and the figures, shorter, and no jargon",
)
QUESTIONS: dict[str, Any] = {
    "grade": {
        "type": "score",
        "instructions": (
            "An expert claims handler decided a travel insurance claim and gave "
            "`handler.reason`, resting on `handler.reference`. Grade how well "
            "`agent.reason` and `agent.section` explain the same decision."
        ),
        "criteria": list(RUBRIC),
    },
    "same_grounds": {
        "type": "noul",
        "instructions": (
            "`agent.reason` gives the same main grounds for the decision "
            "as `handler.reason`"
        ),
    },
    "same_section": {
        "type": "noul",
        "instructions": (
            "`agent.section` refers to the same section of the policy wording "
            "as `handler.reference`"
        ),
    },
}
_finish_locks: dict[int, Lock] = {}


def grade_claim(state: dict[str, Any]) -> dict[str, Any]:
    """Grade one decided claim against its backend-held handler reference."""
    retries = RetryConfig("none", BackoffStrategy(0, 0, 1, 0), False)
    with OpenRouter(
        api_key=os.getenv("OPENROUTER_API_KEY"), timeout_ms=10_000
    ) as client:
        response: Any = client.alpha.decisions.create(
            model="typesafe/jev-1.13",
            state=state,
            questions=QUESTIONS,
            retries=retries,
            timeout_ms=10_000,
            server_url=JUDGE_ORIGIN,
        )
    probabilities = response.answers["grade"].probabilities
    most_likely = max(probabilities, key=probabilities.get)
    return {
        "grade": response.answers["grade"].score,
        "letter": GRADES[int(most_likely)],
        "same_grounds": response.answers["same_grounds"].noul,
        "same_section": response.answers["same_section"].noul,
        "judge_cost": response.usage.cost or 0.0,
    }


def ungraded() -> dict[str, Any]:
    return {
        "grade": None,
        "letter": None,
        "same_grounds": None,
        "same_section": None,
        "judge_cost": 0.0,
    }


def grading_items(run_id: int) -> list[dict[str, Any]]:
    with connect() as connection:
        run = (
            connection.execute(select(runs_table).where(runs_table.c.run_id == run_id))
            .mappings()
            .one()
        )
        if run["kind"] != "evaluation":
            return []
        rows = (
            connection.execute(
                select(claims_table, run_claims_table.c.source_claim_id)
                .join(
                    run_claims_table,
                    claims_table.c.claim_id == run_claims_table.c.claim_id,
                )
                .where(run_claims_table.c.run_id == run_id)
            )
            .mappings()
            .all()
        )
        labels = (
            connection.execute(
                select(handler_decisions_table).where(
                    handler_decisions_table.c.evaluation_set == run["evaluation_set"]
                )
            )
            .mappings()
            .all()
        )
        by_source = {row["claim_id"]: row["data"] for row in labels}
        existing = set(
            connection.execute(
                select(judge_grades_table.c.claim_id).where(
                    judge_grades_table.c.run_id == run_id
                )
            ).scalars()
        )
        return [
            {
                "claim_id": row["claim_id"],
                "run_id": run_id,
                "state": {
                    "handler": {
                        key: by_source[row["source_claim_id"]][key]
                        for key in ("decision", "reason", "reference")
                    },
                    "agent": {
                        "decision": row["decision"],
                        "reason": row["reason"],
                        "section": row["policy_section"],
                    },
                },
            }
            for row in rows
            if row["claim_id"] not in existing
        ]


def grade_item(item: dict[str, Any]) -> dict[str, Any]:
    result = ungraded()
    try:
        check_run(item["run_id"], spending=True)
    except Exception:
        return {"claim_id": item["claim_id"], **result}
    if not os.getenv("MODEL_NAME"):
        if item["state"]["agent"]["decision"] is None:
            result.update(grade=0.0, letter="F", same_grounds=0.0, same_section=0.0)
        else:
            try:
                result = grade_claim(item["state"])
            except Exception:
                result = ungraded()
    return {"claim_id": item["claim_id"], **result}


def grade_evaluation(run_id: int) -> None:
    """Grade an evaluation in parallel without holding a database connection."""
    with _finish_locks.setdefault(run_id, Lock()):
        check_run(run_id)
        items = grading_items(run_id)
        if not items:
            return
        with ThreadPoolExecutor(max_workers=len(items)) as pool:
            results = list(pool.map(grade_item, items))
        with connect() as connection:
            check_run_connection(connection, run_id)
            connection.execute(
                insert(judge_grades_table),
                [{"run_id": run_id, **result} for result in results],
            )
