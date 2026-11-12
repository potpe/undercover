import { useEffect, useState } from "react";
import {
  getSettings,
  getSettingChanges,
  saveSettings,
  type Handler,
  type PlatformSettings,
  type SettingsUpdate,
  type SettingChange,
} from "./api";
import { ConfirmDialog } from "./ConfirmDialog";
import type { Appearance } from "./appearance";
import { SettingsAudit } from "./SettingsAudit";

export function Settings({
  handler,
  appearance,
  onAppearanceChange,
}: Readonly<{
  handler: Handler;
  appearance: Appearance;
  onAppearanceChange: (value: Appearance) => void;
}>) {
  const [settings, setSettings] = useState<PlatformSettings | null>(null);
  const [changes, setChanges] = useState<SettingChange[]>([]);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    Promise.all([getSettings(), getSettingChanges()])
      .then(([data, audit]) => {
        if (active) {
          setSettings(data);
          setChanges(audit);
        }
      })
      .catch(() => {
        if (active) setError("We could not load settings. Try again.");
      });
    return () => {
      active = false;
    };
  }, []);

  async function save() {
    if (!settings) return;
    setConfirm(false);
    setBusy(true);
    const values: SettingsUpdate = {
      approval_limit: settings.approval_limit,
      objectives: settings.objectives,
      noise: settings.noise,
      tolerance: settings.tolerance,
      spending_limit: settings.spending_limit,
      evaluation_concurrency: settings.evaluation_concurrency,
    };
    try {
      const updated = await saveSettings(values);
      const audit = await getSettingChanges();
      setSettings(updated);
      setChanges(audit);
      setMessage("Settings saved.");
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Could not save settings.",
      );
    } finally {
      setBusy(false);
    }
  }

  if (!settings)
    return (
      <p role={error ? "alert" : "status"}>{error || "Loading settings…"}</p>
    );
  const disabled = !handler.can_approve_over_limit || busy;
  return (
    <div className="scoreboard-layout">
      <section className="panel detail-section">
        <p className="eyebrow">Platform controls</p>
        <h1>Settings</h1>
        <p>Set the business limits and objectives used by future work.</p>
        {!handler.can_approve_over_limit ? (
          <p role="note">
            You can view settings, but only a handler who can approve over-limit
            payouts can change them.
          </p>
        ) : null}
        <form
          onSubmit={(event) => {
            event.preventDefault();
            setConfirm(true);
          }}
        >
          <fieldset disabled={disabled} className="settings-fields">
            {(
              [
                ["approval_limit", "Approval limit (USD)"],
                ["spending_limit", "Round spending limit (USD)"],
                ["evaluation_concurrency", "Evaluation agents at once"],
              ] as const
            ).map(([key, label]) => (
              <label key={key}>
                {label}
                <input
                  type="number"
                  min={key === "evaluation_concurrency" ? "1" : "0.01"}
                  max={key === "evaluation_concurrency" ? "25" : undefined}
                  step={key === "evaluation_concurrency" ? "1" : "0.01"}
                  value={settings[key]}
                  onChange={(event) => {
                    setSettings({
                      ...settings,
                      [key]: Number(event.target.value),
                    });
                  }}
                />
              </label>
            ))}
            {(["objectives", "noise", "tolerance"] as const).map((group) => (
              <fieldset key={group}>
                <legend>
                  {group.charAt(0).toUpperCase() + group.slice(1)}
                </legend>
                {Object.entries(settings[group]).map(([key, value]) => (
                  <label key={key}>
                    {group} {key.replaceAll("_", " ")}
                    <input
                      type="number"
                      min="0"
                      step="0.01"
                      value={Number(value)}
                      onChange={(event) => {
                        setSettings({
                          ...settings,
                          [group]: {
                            ...settings[group],
                            [key]: Number(event.target.value),
                          },
                        });
                      }}
                    />
                  </label>
                ))}
              </fieldset>
            ))}
          </fieldset>
          {handler.can_approve_over_limit ? (
            <button type="submit" disabled={busy} className="primary-action">
              Save settings
            </button>
          ) : null}
        </form>
        {message ? <p role="status">{message}</p> : null}
        {error ? <p role="alert">{error}</p> : null}
      </section>
      <section className="panel detail-section">
        <h2>Models allowed</h2>
        <p>Maintained in the seed data. Prices are USD per million tokens.</p>
        <table>
          <thead>
            <tr>
              <th>Model</th>
              <th>Reasoning</th>
              <th>Input</th>
              <th>Output</th>
            </tr>
          </thead>
          <tbody>
            {settings.models_allowed.map((model) => (
              <tr key={model.model_name}>
                <td className="mono">{model.model_name}</td>
                <td>{model.reasoning_efforts.join(", ")}</td>
                <td>{model.input_price.toFixed(2)}</td>
                <td>{model.output_price.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className="panel detail-section">
        <h2>Appearance</h2>
        <p>Remembered in this browser only.</p>
        <label htmlFor="appearance">Appearance</label>
        <select
          id="appearance"
          value={appearance}
          onChange={(event) => {
            onAppearanceChange(event.target.value as Appearance);
          }}
        >
          <option value="system">Same as computer</option>
          <option value="light">Light</option>
          <option value="dark">Dark</option>
        </select>
      </section>
      <SettingsAudit changes={changes} />
      {confirm ? (
        <ConfirmDialog
          title="Change platform settings?"
          onCancel={() => {
            setConfirm(false);
          }}
          onConfirm={() => {
            void save();
          }}
        />
      ) : null}
    </div>
  );
}
