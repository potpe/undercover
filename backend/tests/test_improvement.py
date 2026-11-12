import json

import pytest
from sqlalchemy import insert, select, update

from backend.database import (
    connect,
    handler_decisions_table,
    held_out_claims_table,
    runs_table,
    settings_table,
    timestamp,
)
from backend.improvement import (
    create_round,
    get_round,
    recover_rounds,
    stop_round,
    verdict,
)
from backend.improvement_proposals import apply_proposal, decide_proposal, save_proposal
from backend.improvement_rules import compare_scores
from backend.improver_results import read_results
from backend.settings import get_settings, update_settings
from backend.version_scores import average_scores


def scores(**changes):
    return {
        "accuracy": 0.7,
        "grade": 1.5,
        "cost_per_claim": 0.01,
        "seconds_per_claim": 10.0,
        **changes,
    }


@pytest.mark.parametrize(
    ("new", "kept", "reason"),
    [
        (scores(accuracy=0.85), True, "accuracy"),
        (scores(accuracy=0.8), False, "noise"),
        (scores(grade=None), False, "grade"),
        (scores(accuracy=0.5, grade=2.0), False, "objectives"),
        (scores(grade=2.0, cost_per_claim=0.021), False, "cost_per_claim"),
        (scores(grade=2.0, seconds_per_claim=12.6), False, "seconds_per_claim"),
        (scores(cost_per_claim=0.008), True, "cost_per_claim"),
        (scores(seconds_per_claim=8), True, "seconds_per_claim"),
        (scores(grade=1.65), False, "noise"),
        (scores(grade=1.66), True, "grade"),
        (scores(cost_per_claim=0.0085), False, "noise"),
        (scores(seconds_per_claim=8.5), False, "noise"),
        (scores(accuracy=0.6, grade=2), True, "grade"),
        (scores(accuracy=0.9, grade=1.35), True, "accuracy"),
        (scores(accuracy=0.9, grade=1.34), False, "grade"),
        (scores(accuracy=0.9, cost_per_claim=0.02), True, "accuracy"),
        (scores(accuracy=0.9, seconds_per_claim=12.5), True, "accuracy"),
    ],
)
def test_verdict_matches_hand_worked_differences_and_fractions(
    client, new, kept, reason
):
    result = compare_scores(new, scores(), get_settings())
    assert result["kept"] is kept
    assert reason in result["reason"]


@pytest.mark.parametrize(
    ("old", "new", "measure"),
    [
        (scores(accuracy=0.9), scores(accuracy=0.79), "accuracy"),
        (scores(grade=2.0), scores(grade=1.84), "grade"),
        (scores(), scores(cost_per_claim=0.0201), "cost_per_claim"),
        (scores(), scores(seconds_per_claim=12.51), "seconds_per_claim"),
    ],
)
def test_each_measure_regression_beyond_tolerance_rolls_back(client, old, new, measure):
    result = compare_scores(new, old, get_settings())
    assert result["kept"] is False
    assert result["reason"] == f"Worse beyond tolerance: {measure}"


def test_round_preserves_settings_and_only_one_is_open(client):
    initial = create_round("HND-01")
    assert initial["starting_version"] == 1
    settings = get_settings()
    models_allowed = settings["models_allowed"]
    settings.pop("models_allowed")
    settings["spending_limit"] = 0.75
    update_settings(settings, "HND-01")
    changed_models = [
        {**models_allowed[0], "input_price": models_allowed[0]["input_price"] + 0.25},
        *models_allowed[1:],
    ]
    with connect() as connection:
        connection.execute(
            update(settings_table)
            .where(settings_table.c.key == "models_allowed")
            .values(data=changed_models)
        )
    assert get_round(initial["round_id"])["settings"]["spending_limit"] == 0.5
    assert (
        get_round(initial["round_id"])["settings"]["models_allowed"] == models_allowed
    )
    with pytest.raises(Exception, match="already open"):
        create_round("HND-01")
    stop_round(initial["round_id"])
    next_round = create_round("HND-01")
    assert next_round["settings"]["spending_limit"] == 0.75
    assert next_round["settings"]["models_allowed"] == changed_models
    assert next_round["thread_id"] != initial["thread_id"]


def approve_candidate(round_id):
    record = save_proposal(
        round_id,
        "configuration",
        {
            "instructions": "Check coverage carefully.",
            "model": "openai/gpt-6-luna",
            "reasoning_effort": "medium",
            "reason": "Improve accuracy",
        },
    )
    proposal_id = record["proposals"][-1]["proposal_id"]
    decide_proposal(
        round_id,
        {
            "proposal_id": proposal_id,
            "decision": "approve",
            "reason": "",
            "handler_id": "HND-01",
        },
    )
    return apply_proposal(round_id, "configuration")


def test_proposals_change_nothing_before_approval_and_rejections_are_audited(client):
    round_id = create_round("HND-01")["round_id"]
    payload = {
        "instructions": "Check coverage carefully.",
        "model": "openai/gpt-6-luna",
        "reasoning_effort": "low",
        "reason": "Improve accuracy",
    }
    record = save_proposal(round_id, "configuration", payload)
    assert len(client.get("/api/configuration").json()["versions"]) == 1
    with pytest.raises(Exception, match="not been approved"):
        apply_proposal(round_id, "configuration")
    decide_proposal(
        round_id,
        {
            "proposal_id": record["proposals"][0]["proposal_id"],
            "decision": "reject",
            "reason": "Use a general rule",
            "handler_id": "HND-01",
        },
    )
    assert (
        get_round(round_id)["proposals"][0]["decision_reason"] == "Use a general rule"
    )
    approve_candidate(round_id)
    assert len(client.get("/api/configuration").json()["versions"]) == 2
    assert client.get("/api/configuration").json()["current_version"] == 1
    apply_proposal(round_id, "configuration")
    assert len(client.get("/api/configuration").json()["versions"]) == 2


def test_invalid_model_does_not_create_a_handler_proposal(client):
    round_id = create_round("HND-01")["round_id"]
    with pytest.raises(Exception, match="not allowed"):
        save_proposal(
            round_id,
            "configuration",
            {
                "instructions": "Check coverage",
                "model": "not-allowed",
                "reasoning_effort": "low",
                "reason": "Try it",
            },
        )
    assert get_round(round_id)["proposals"] == []


def snapshot_run(round_id, version, evaluation_set, values, **changes):
    with connect() as connection:
        connection.execute(
            insert(runs_table).values(
                kind="evaluation",
                concurrency=25,
                configuration_version=version,
                round_id=round_id,
                round_purpose="candidate" if version > 1 else "baseline",
                model_name="openai/gpt-6-luna",
                reasoning_effort="low",
                evaluation_set=evaluation_set,
                tiny=changes.get("tiny", False),
                status=changes.get("status", "completed"),
                scores=values,
                created_at=timestamp(),
            )
        )


def test_only_completed_whole_round_runs_contribute_to_scores(client):
    round_id = create_round("HND-01")["round_id"]
    snapshot_run(round_id, 1, "held_out", scores(accuracy=0.6))
    snapshot_run(round_id, 1, "held_out", scores(accuracy=0.8))
    snapshot_run(None, 1, "held_out", scores(accuracy=0))
    snapshot_run(round_id, 1, "held_out", scores(accuracy=0), tiny=True)
    snapshot_run(round_id, 1, "held_out", scores(accuracy=0), status="stopped")
    assert average_scores(1, "held_out") == scores()


def test_kept_verdict_survives_stop(client):
    round_id = create_round("HND-01")["round_id"]
    snapshot_run(round_id, 1, "held_out", scores())
    snapshot_run(round_id, 1, "held_out", scores())
    approve_candidate(round_id)
    for name in ("evaluation", "held_out", "held_out"):
        snapshot_run(round_id, 2, name, scores(accuracy=0.9))
    result = verdict(round_id)
    assert result["verdict"] == "kept"
    assert result["held_out_before"] == scores()
    assert result["held_out_after"] == scores(accuracy=0.9)
    assert result["verdict_reason"] == "Improved: accuracy"
    stop_round(round_id)
    assert client.get("/api/configuration").json()["current_version"] == 2


def test_startup_stops_an_unverdict_round_without_changing_current(client):
    round_id = create_round("HND-01")["round_id"]
    approve_candidate(round_id)

    recover_rounds()

    record = get_round(round_id)
    assert record["status"] == "stopped"
    assert record["verdict"] == "rolled_back"
    assert record["verdict_reason"] == "Round stopped before its verdict"
    assert client.get("/api/configuration").json()["current_version"] == 1


def test_startup_stop_preserves_a_kept_verdict(client):
    round_id = create_round("HND-01")["round_id"]
    snapshot_run(round_id, 1, "held_out", scores())
    snapshot_run(round_id, 1, "held_out", scores())
    approve_candidate(round_id)
    for name in ("evaluation", "held_out", "held_out"):
        snapshot_run(round_id, 2, name, scores(accuracy=0.9))
    assert verdict(round_id)["verdict"] == "kept"

    recover_rounds()

    assert get_round(round_id)["status"] == "stopped"
    assert get_round(round_id)["verdict"] == "kept"
    assert client.get("/api/configuration").json()["current_version"] == 2


@pytest.mark.parametrize("failure", ["no_grade", "stopped", "unaffordable"])
def test_candidate_failures_roll_back_without_changing_current(client, failure):
    round_id = create_round("HND-01")["round_id"]
    snapshot_run(round_id, 1, "held_out", scores())
    approve_candidate(round_id)
    for name in ("evaluation", "held_out", "held_out"):
        snapshot_run(
            round_id,
            2,
            name,
            scores(accuracy=0.9, grade=None if failure == "no_grade" else 2),
            status="stopped" if failure == "stopped" else "completed",
        )
    record = verdict(
        round_id,
        "Evaluation estimate exceeds remaining budget"
        if failure == "unaffordable"
        else None,
    )
    assert record["verdict"] == "rolled_back"
    assert (
        record["verdict_reason"]
        == {
            "no_grade": "A version has no grade",
            "stopped": "Required evaluations did not complete",
            "unaffordable": "Evaluation estimate exceeds remaining budget",
        }[failure]
    )
    assert record["held_out_before"] == scores()
    if failure == "stopped":
        assert record["held_out_after"] is None
    else:
        assert record["held_out_after"] is not None
    assert client.get("/api/configuration").json()["current_version"] == 1


@pytest.mark.parametrize(
    "path", ["/api/rounds", "/api/rounds/1/stop", "/api/rounds/1/decision"]
)
@pytest.mark.parametrize("token", ["marcus-token", "priya-token"])
def test_round_handler_mutations_require_senior_permission(client, path, token):
    record = create_round("HND-01")
    round_id = record["round_id"]
    pending = save_proposal(
        round_id,
        "configuration",
        {
            "instructions": "Check the policy and all supporting documents.",
            "model": "openai/gpt-6-luna",
            "reasoning_effort": "low",
            "reason": "Keep the decision evidence-based",
        },
    )
    proposal_id = pending["proposals"][0]["proposal_id"]
    path = path.replace("/1/", f"/{round_id}/")
    body = (
        {"proposal_id": proposal_id, "decision": "approve", "reason": ""}
        if path.endswith("decision")
        else None
    )
    before = get_round(round_id)
    client.headers.pop("Authorization", None)
    assert client.post(path, json=body).status_code == 401
    client.headers["Authorization"] = f"Bearer {token}"
    assert client.post(path, json=body).status_code == 403
    assert get_round(round_id) == before


def test_improver_view_contains_only_held_out_aggregates(client):
    round_id = create_round("HND-01")["round_id"]
    evaluation_run_id = None
    for evaluation_set in ("evaluation", "held_out"):
        run = client.post(
            "/api/internal/evaluations",
            json={
                "concurrency": 25,
                "configuration_version": 1,
                "model_name": "openai/gpt-6-luna",
                "reasoning_effort": "low",
                "round_id": round_id,
                "round_purpose": "baseline",
                "evaluation_set": evaluation_set,
                "tiny": False,
            },
        ).json()
        if evaluation_set == "evaluation":
            evaluation_run_id = run["run_id"]
        client.post(f"/api/internal/runs/{run['run_id']}/finish")
    with connect() as connection:
        held_out = (
            connection.execute(select(held_out_claims_table).limit(1)).mappings().one()
        )
        private_claim_id = held_out["claim_id"]
        data = {**held_out["data"], "summary": "__held_out_private_summary__"}
        connection.execute(
            update(held_out_claims_table)
            .where(held_out_claims_table.c.claim_id == held_out["claim_id"])
            .values(data=data)
        )
        label = (
            connection.execute(
                select(handler_decisions_table)
                .where(handler_decisions_table.c.evaluation_set == "held_out")
                .limit(1)
            )
            .mappings()
            .one()
        )
        label_data = {
            **label["data"],
            "reason": "__held_out_private_reason__",
            "reference": "__held_out_private_reference__",
        }
        connection.execute(
            update(handler_decisions_table)
            .where(
                handler_decisions_table.c.evaluation_set == label["evaluation_set"],
                handler_decisions_table.c.claim_id == label["claim_id"],
            )
            .values(data=label_data)
        )
    report = read_results(round_id)
    assert report["qualifying_counts"] == {"evaluation": 1, "held_out": 1}
    assert set(report["scores"]) == {"evaluation", "held_out"}
    assert report["scores"]["held_out"] is not None
    assert set(report["scores"]["held_out"]) == {
        "accuracy",
        "grade",
        "cost_per_claim",
        "seconds_per_claim",
    }
    assert report["evaluation_set"]["wrong"]
    assert len(report["evaluation_set"]["explained_least_well"]) == 5
    assert report["current_version"]["version"] == 1
    assert report["current_version"]["model_name"] == "openai/gpt-6-luna"
    assert report["earlier_rounds"] == []
    assert report["objectives"] == {"accuracy": 0.6, "grade": 1.0}
    assert report["noise"]["accuracy"] == 0.1
    assert report["tolerance"]["seconds_per_claim"] == 0.25
    assert report["rule"] == (
        "Meet both objectives, improve at least one measure beyond noise, "
        "and worsen no measure beyond tolerance"
    )
    assert (
        report["models_allowed"] == client.get("/api/settings").json()["models_allowed"]
    )
    round_record = get_round(round_id)
    assert report["budget_left"] == pytest.approx(
        max(0, round_record["settings"]["spending_limit"] - round_record["cost"])
    )
    evaluation = client.get(f"/api/evaluations/{evaluation_run_id}").json()
    comparisons = evaluation["claim_comparisons"]
    expected_wrong = [row["source_claim_id"] for row in comparisons if not row["right"]]
    expected_least = sorted(
        (row for row in comparisons if row["right"]),
        key=lambda row: (row["grade"] is not None, row["grade"] or 0),
    )[:5]
    assert [row["claim_id"] for row in report["evaluation_set"]["wrong"]] == (
        expected_wrong
    )
    assert [
        row["claim_id"] for row in report["evaluation_set"]["explained_least_well"]
    ] == [row["source_claim_id"] for row in expected_least]
    for row in (
        report["evaluation_set"]["wrong"]
        + report["evaluation_set"]["explained_least_well"]
    ):
        assert set(row) == {"claim_id", "agent", "handler", "judge"}
        assert set(row["handler"]) == {"decision", "payout", "reason", "reference"}
        assert set(row["judge"]) == {
            "grade",
            "letter",
            "same_grounds",
            "same_section",
        }
    encoded = json.dumps(report)
    assert private_claim_id not in encoded
    assert "__held_out_private_summary__" not in encoded
    assert "__held_out_private_reason__" not in encoded
    assert "__held_out_private_reference__" not in encoded
    assert set(report["scores"]["held_out"]) == {
        "accuracy",
        "grade",
        "cost_per_claim",
        "seconds_per_claim",
    }
