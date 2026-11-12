import { useEffect, useState } from "react";
import type { ConfigurationHistory, Handler, RecipeHistory } from "./api";
import {
  decideProposal,
  getConfiguration,
  getRecipes,
  listRounds,
  startRound,
  stopRound,
  type ImprovementRound,
} from "./improvement-api";
import { ConfirmDialog } from "./ConfirmDialog";
import { DiffView } from "./DiffView";
import { ProposalReview } from "./ProposalReview";
import { RoundHistory } from "./RoundHistory";
import { VersionChart } from "./VersionChart";
import { roundProgress } from "./round-stage";

interface History {
  rounds: ImprovementRound[];
  recipes: RecipeHistory;
  configuration: ConfigurationHistory;
}

export function Improvement({ handler }: Readonly<{ handler: Handler }>) {
  const [history, setHistory] = useState<History | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<"start" | "stop" | null>(null);
  useEffect(() => {
    let active = true;
    let fetching = false;
    async function refresh() {
      if (fetching) return;
      fetching = true;
      try {
        const [rounds, recipes, configuration] = await Promise.all([
          listRounds(),
          getRecipes(),
          getConfiguration(),
        ]);
        if (active) setHistory({ rounds, recipes, configuration });
      } catch (cause) {
        if (active)
          setError(
            cause instanceof Error
              ? cause.message
              : "Could not load improvement history. Try again.",
          );
      } finally {
        fetching = false;
      }
    }
    void refresh();
    const timer = setInterval(() => {
      void refresh();
    }, 2000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

  async function perform(action: () => Promise<ImprovementRound>) {
    setBusy(true);
    setError("");
    try {
      const round = await action();
      setHistory((current) =>
        current
          ? {
              ...current,
              rounds: [
                round,
                ...current.rounds.filter(
                  (item) => item.round_id !== round.round_id,
                ),
              ].sort((a, b) => b.round_id - a.round_id),
            }
          : current,
      );
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Could not complete the action. Try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  const open = history?.rounds.find((round) => round.status === "open");
  return (
    <div className="scoreboard-layout">
      <section className="panel detail-section">
        <p className="eyebrow">Controlled improvement</p>
        <h1>Improvement</h1>
        <p>
          Review proposed changes, test them against held-out claims and keep
          only measured improvements.
        </p>
        {!handler.can_approve_over_limit ? (
          <p role="note">
            Only handlers who can approve over-limit payouts may start or stop
            rounds, or approve or reject proposals. You can review all results
            and recipes.
          </p>
        ) : (
          <div className="run-controls">
            <button
              type="button"
              className="primary-action"
              disabled={busy || !history || !!open}
              onClick={() => {
                setConfirm("start");
              }}
            >
              Start round
            </button>
            {open ? (
              <button
                type="button"
                className="secondary-action"
                disabled={busy}
                onClick={() => {
                  setConfirm("stop");
                }}
              >
                Stop round
              </button>
            ) : null}
          </div>
        )}
        <div className="round-progress" role="status">
          {roundProgress(open)}
        </div>
        {error ? <p role="alert">{error}</p> : null}
      </section>
      {!history ? (
        <p role="status">Loading improvement history…</p>
      ) : (
        <ImprovementHistory
          history={history}
          handler={handler}
          busy={busy}
          onDecision={(roundId, proposalId, decision, reason) =>
            perform(() => decideProposal(roundId, proposalId, decision, reason))
          }
        />
      )}
      {confirm ? (
        <ConfirmDialog
          title={
            confirm === "start"
              ? "Start an improvement round?"
              : "Stop this round?"
          }
          description={
            confirm === "start"
              ? "This round uses the current settings and may spend up to its round limit. You will review proposals before they take effect."
              : "New work will stop. A version already kept by its verdict stays current; an untested candidate will not become current."
          }
          confirmLabel={confirm === "start" ? "Confirm start" : "Confirm stop"}
          onCancel={() => {
            setConfirm(null);
          }}
          onConfirm={() => {
            setConfirm(null);
            void perform(
              confirm === "start"
                ? startRound
                : () => stopRound(open?.round_id ?? 0),
            );
          }}
        />
      ) : null}
    </div>
  );
}

function ImprovementHistory({
  history,
  handler,
  busy,
  onDecision,
}: Readonly<{
  history: History;
  handler: Handler;
  busy: boolean;
  onDecision: (
    roundId: number,
    proposalId: number,
    decision: "approve" | "reject",
    reason: string,
  ) => Promise<void>;
}>) {
  return (
    <>
      {history.rounds
        .filter((round) => round.status === "open")
        .map((round) =>
          round.proposals
            .filter((proposal) => proposal.status === "pending")
            .map((proposal) =>
              handler.can_approve_over_limit ? (
                <ProposalReview
                  key={proposal.proposal_id}
                  proposal={proposal}
                  previous={history.configuration.versions.find(
                    (version) => version.version === round.starting_version,
                  )}
                  busy={busy}
                  onDecision={(decision, reason) =>
                    onDecision(
                      round.round_id,
                      proposal.proposal_id,
                      decision,
                      reason,
                    )
                  }
                />
              ) : (
                <section
                  className="panel detail-section"
                  key={proposal.proposal_id}
                >
                  <h2>Proposal waiting for a senior handler</h2>
                  <p>{proposal.reason}</p>
                  <DiffView lines={proposal.diff} />
                </section>
              ),
            ),
        )}
      {!history.rounds.length ? (
        <section className="panel detail-section">
          <h2>Round history</h2>
          <p>No rounds yet.</p>
          <p>
            A senior handler can start the first round to evaluate the current
            version.
          </p>
        </section>
      ) : null}
      <VersionChart rounds={history.rounds} />
      {history.rounds.map((round) => (
        <RoundHistory key={round.round_id} round={round} />
      ))}
      <section className="panel detail-section">
        <h2>Recipe versions</h2>
        <p>
          Current recipe: {history.recipes.current_version}. Each round keeps
          the recipe it started with.
        </p>
        {history.recipes.versions.map((recipe) => (
          <article key={recipe.version}>
            <h3>Recipe {recipe.version}</h3>
            <p>{recipe.reason}</p>
            <p className="muted">
              Proposed by {recipe.proposed_by}; approved by{" "}
              {recipe.approved_by ?? "Initial seed"}
              {recipe.approved_at
                ? ` at ${new Date(recipe.approved_at).toLocaleString()}`
                : ""}
              .
            </p>
            <details>
              <summary>Recipe text</summary>
              <pre className="configuration-text">{recipe.recipe}</pre>
            </details>
            {recipe.version === 1 ? (
              <p>No earlier recipe to compare.</p>
            ) : (
              <DiffView lines={recipe.diff} />
            )}
          </article>
        ))}
      </section>
    </>
  );
}
