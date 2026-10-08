import React, { useEffect, useState } from "react";
import {
  ArrowRight,
  LoaderCircle,
  LockKeyhole,
  ShieldCheck,
  Store,
  Zap,
} from "lucide-react";
import { authenticatedFetch, setSession } from "./session.js";
import "./auth.css";

export default function AuthGate({ children }) {
  const [admin, setAdmin] = useState(null),
    [checking, setChecking] = useState(true),
    [busy, setBusy] = useState(false);
  const [username, setUsername] = useState(""),
    [password, setPassword] = useState(""),
    [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    function expired() {
      setAdmin(null);
      setSession(null);
    }
    window.addEventListener("fynd-session-expired", expired);
    authenticatedFetch("/api/auth/session", {
      signal: AbortSignal.timeout(8000),
    })
      .then(async (r) => {
        if (r.status === 401) return;
        if (!r.ok)
          throw new Error(
            "Unable to check your session. Please try signing in.",
          );
        const session = await r.json();
        if (session.role !== "admin")
          throw new Error("Administrator access required.");
        if (active) {
          setSession(session);
          setAdmin(session);
        }
      })
      .catch((e) => {
        if (active) setError(e.message);
      })
      .finally(() => {
        if (active) setChecking(false);
      });
    return () => {
      active = false;
      window.removeEventListener("fynd-session-expired", expired);
    };
  }, []);
  async function login(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const response = await authenticatedFetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
        signal: AbortSignal.timeout(15000),
      });
      const value = await response.json();
      if (!response.ok)
        throw new Error(
          typeof value.detail === "string"
            ? value.detail
            : "Unable to sign in. Check your credentials.",
        );
      if (value.role !== "admin")
        throw new Error("Administrator access required.");
      setSession(value);
      setAdmin(value);
      setPassword("");
    } catch (e) {
      setError(
        e.name === "TimeoutError"
          ? "Sign-in timed out. Please try again."
          : e.message,
      );
    } finally {
      setBusy(false);
    }
  }
  async function logout() {
    const response = await authenticatedFetch("/api/auth/logout", {
      method: "POST",
      signal: AbortSignal.timeout(8000),
    });
    if (!response.ok && response.status !== 401)
      throw new Error("Could not sign out. Please try again.");
    setSession(null);
    setAdmin(null);
    setPassword("");
    setError("");
  }
  if (checking)
    return (
      <div className="auth-loading" role="status">
        <LoaderCircle className="spin" size={24} />
        Checking your session…
      </div>
    );
  if (admin) return children({ admin, logout });
  return (
    <main className="auth-page">
      <section className="auth-story">
        <div className="auth-brand">
          <span className="brand-icon">
            <Zap size={22} fill="currentColor" />
          </span>
          fynd.
        </div>
        <div>
          <span className="eyebrow">OPERATIONS CONSOLE</span>
          <h1>
            Your stores.
            <br />
            One clear view.
          </h1>
          <p>
            Monitor order movement, understand exceptions, and verify every
            update with confidence.
          </p>
          <div className="auth-story-cards">
            <span>
              <Store size={19} />
              Store operations
            </span>
            <span>
              <ShieldCheck size={19} />
              Verified order state
            </span>
          </div>
        </div>
        <small>Mock Store Network · JioMart OS</small>
      </section>
      <section className="auth-form-panel">
        <form onSubmit={login} className="auth-form">
          <span className="auth-lock">
            <LockKeyhole size={23} />
          </span>
          <span className="eyebrow">ADMIN ACCESS</span>
          <h2>Welcome back.</h2>
          <p>Sign in to your operations workspace.</p>
          <label htmlFor="admin-username">Username</label>
          <input
            id="admin-username"
            name="username"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            maxLength={100}
            autoFocus
            placeholder="Enter your username"
          />
          <label htmlFor="admin-password">Password</label>
          <input
            id="admin-password"
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            maxLength={256}
            placeholder="Enter your password"
          />
          {error && (
            <div className="auth-error" role="alert">
              {error}
            </div>
          )}
          <button className="button primary" type="submit" disabled={busy}>
            {busy ? (
              <LoaderCircle className="spin" size={16} />
            ) : (
              <ArrowRight size={16} />
            )}{" "}
            {busy ? "Signing in…" : "Sign in"}
          </button>
          <small>
            <ShieldCheck size={13} />
            Administrator sign-in only
          </small>
        </form>
      </section>
    </main>
  );
}
