import type { AgentRun, RunTodo } from "./api";

const todoLabels: Record<RunTodo["status"], string> = {
  to_do: "To do",
  in_progress: "In progress",
  done: "Done",
  left_for_handler: "Left for handler",
};

function RunTodos({
  todos,
  onSelect,
}: Readonly<{ todos: RunTodo[]; onSelect: (claimId: string) => void }>) {
  if (!todos.length) return <p className="muted">Planning claims…</p>;
  return (
    <ol className="todo-list">
      {todos.map((todo) => (
        <li key={todo.claim_id}>
          <span className="todo-position mono">{todo.position}</span>
          <div className="todo-content">
            <button
              type="button"
              className="claim-link mono"
              aria-label={`View claim ${todo.claim_id}`}
              onClick={() => {
                onSelect(todo.claim_id);
              }}
            >
              {todo.claim_id}
            </button>
            {todo.result && !todo.error ? <span>{todo.result}</span> : null}
            {todo.error ? (
              <span className="todo-error">{todo.error}</span>
            ) : null}
          </div>
          <div className="todo-meta">
            {todo.attempts ? <span>{todo.attempts} attempts</span> : null}
            <span className={`todo-status todo-${todo.status}`}>
              {todoLabels[todo.status]}
            </span>
          </div>
        </li>
      ))}
    </ol>
  );
}

export function RunPanel({
  run,
  cap,
  busy,
  error,
  onCapChange,
  onRun,
  onSelect,
}: Readonly<{
  run: AgentRun | null;
  cap: string;
  busy: boolean;
  error: string;
  onCapChange: (cap: string) => void;
  onRun: () => Promise<void>;
  onSelect: (claimId: string) => void;
}>) {
  const active = run?.status === "in_progress";
  const done = run?.todos.filter((todo) => todo.status === "done").length ?? 0;

  return (
    <section className="panel run-panel" aria-labelledby="run-title">
      <div className="detail-heading">
        <div>
          <p className="eyebrow">Autonomous work</p>
          <h2 id="run-title">Current run</h2>
        </div>
        {run ? (
          <span className={`run-state run-${run.status}`}>
            {run.status.replaceAll("_", " ")}
          </span>
        ) : null}
      </div>
      <div className="run-controls">
        <label htmlFor="claim-cap">Claim cap</label>
        <input
          id="claim-cap"
          type="number"
          min={1}
          max={25}
          step={1}
          value={cap}
          disabled={busy || active}
          onChange={(event) => {
            onCapChange(event.target.value);
          }}
        />
        <button
          type="button"
          className="primary-action"
          disabled={busy || active}
          onClick={() => {
            void onRun();
          }}
        >
          {busy ? "Starting…" : "Run queue"}
        </button>
      </div>
      {error ? <p role="alert">{error}</p> : null}
      {run ? (
        <div className="run-details">
          <div className="run-meta">
            <span className="mono">Run {run.run_id}</span>
            <span>
              {done} of {run.todos.length} done
            </span>
            {run.model_name ? (
              <span className="mono">{run.model_name}</span>
            ) : null}
          </div>
          {run.summary ? <p className="run-summary">{run.summary}</p> : null}
          <RunTodos todos={run.todos} onSelect={onSelect} />
        </div>
      ) : (
        <p className="muted">
          No queue run yet. Choose a cap and start the agent.
        </p>
      )}
    </section>
  );
}
