"""The improver's evaluation-only detail view and held-out aggregate scores."""

from typing import Any

from backend.configurations import agent_configuration
from backend.database import connect
from backend.evaluations import evaluation_record
from backend.improvement import get_round, list_rounds
from backend.version_scores import average_scores, qualifying_runs


def read_results(round_id: int) -> dict[str, Any]:
    record = get_round(round_id)
    version = (
        record["candidate_version"]
        if record["verdict"] == "kept"
        else record["starting_version"]
    )
    with connect() as connection:
        runs = qualifying_runs(connection, version, "evaluation")
    rows = evaluation_record(runs[-1]["run_id"])["claim_comparisons"] if runs else []
    wrong = [row for row in rows if not row["right"]]
    right = sorted(
        [row for row in rows if row["right"]],
        key=lambda row: (row["grade"] is not None, row["grade"] or 0),
    )[:5]

    def beside_handler(row):
        return {
            "claim_id": row["source_claim_id"],
            "agent": {
                "decision": row["agent_decision"],
                "payout": row["agent_payout"],
                "reason": row["agent_reason"],
                "section": row["agent_section"],
            },
            "handler": {
                "decision": row["handler_decision"],
                "payout": row["handler_payout"],
                "reason": row["handler_reason"],
                "reference": row["handler_reference"],
            },
            "judge": {
                key: row[key]
                for key in ("grade", "letter", "same_grounds", "same_section")
            },
        }

    settings = record["settings"]
    return {
        "current_version": agent_configuration(version),
        "scores": {
            name: average_scores(version, name) for name in ("evaluation", "held_out")
        },
        "qualifying_counts": {
            name: count_runs(version, name) for name in ("evaluation", "held_out")
        },
        "evaluation_set": {
            "wrong": [beside_handler(row) for row in wrong],
            "explained_least_well": [beside_handler(row) for row in right],
        },
        "earlier_rounds": [
            {
                key: item[key]
                for key in (
                    "round_id",
                    "recipe_version",
                    "candidate_version",
                    "verdict",
                    "verdict_reason",
                    "held_out_before",
                    "held_out_after",
                )
            }
            for item in list_rounds()
            if item["round_id"] < round_id
        ],
        "objectives": settings["objectives"],
        "noise": settings["noise"],
        "tolerance": settings["tolerance"],
        "rule": (
            "Meet both objectives, improve at least one measure beyond noise, "
            "and worsen no measure beyond tolerance"
        ),
        "budget_left": max(0, settings["spending_limit"] - record["cost"]),
        "models_allowed": settings["models_allowed"],
    }


def count_runs(version: int, evaluation_set: str) -> int:
    with connect() as connection:
        return len(qualifying_runs(connection, version, evaluation_set))
