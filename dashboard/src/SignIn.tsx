import { useState, type SyntheticEvent } from "react";
import type { Session } from "@supabase/supabase-js";
import { supabase } from "./supabase";

export function SignIn({
  onSignedIn,
}: Readonly<{ onSignedIn: (session: Session) => void }>) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: SyntheticEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const { data, error: signInError } = await supabase.auth.signInWithPassword(
      { email, password },
    );
    if (signInError) setError(signInError.message);
    else onSignedIn(data.session);
    setBusy(false);
  }

  return (
    <main className="sign-in-page">
      <section className="panel sign-in-panel" aria-labelledby="sign-in-title">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            UC
          </span>
          <div>
            <span className="brand-name">UnderCover</span>
            <span className="brand-product">Claims platform</span>
          </div>
        </div>
        <p className="eyebrow">Claims operations</p>
        <h1 id="sign-in-title">Sign in to UnderCover</h1>
        <p>Use your claims handler email address and password.</p>
        <form
          onSubmit={(event) => {
            void submit(event);
          }}
        >
          <label htmlFor="email">Email address</label>
          <input
            id="email"
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(event) => {
              setEmail(event.target.value);
            }}
          />
          <label htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => {
              setPassword(event.target.value);
            }}
          />
          {error ? <p role="alert">{error}</p> : null}
          <button className="primary-action" disabled={busy} type="submit">
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </section>
    </main>
  );
}
