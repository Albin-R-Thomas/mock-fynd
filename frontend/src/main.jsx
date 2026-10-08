import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  ArrowDownToLine,
  ArrowRight,
  ArrowUpRight,
  Box,
  Check,
  CheckCheck,
  ChevronDown,
  ChevronRight,
  Circle,
  CircleCheck,
  Clock3,
  Code2,
  Database,
  FlaskConical,
  LayoutDashboard,
  LoaderCircle,
  Package,
  Pause,
  Play,
  Radio,
  RefreshCw,
  Search,
  Server,
  ShieldCheck,
  Store,
  Terminal,
  Truck,
  X,
  Zap,
  AlertTriangle,
  RotateCcw,
} from "lucide-react";
import "./styles.css";
import TestStoreDashboard from "./TestStoreDashboard.jsx";
import MockStoreSummary from "./MockStoreSummary.jsx";
import AuthGate from "./AuthGate.jsx";
import { authenticatedFetch } from "./session.js";

const STATUS = ["open", "packed", "out_for_delivery", "delivered", "returned"];
const LABELS = {
  open: "Open",
  packed: "Packed",
  out_for_delivery: "Out for delivery",
  delivered: "Delivered",
  returned: "Returned",
};
const STORE_NAMES = {
  "store-001": "Central Market",
  "store-002": "Riverside Express",
  "store-003": "Park Avenue",
};
async function api(path, options = {}) {
  const response = await authenticatedFetch("/api" + path, {
    ...options,
    headers: { "Content-Type": "application/json" },
    signal: options.signal || AbortSignal.timeout(115000),
  });
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error(
      "The server returned an unreadable response. Check the backend connection.",
    );
  }
  if (!response.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : `Request failed (${response.status})`,
    );
  return data;
}
const post = (path) => api(path, { method: "POST" });
const num = (n) => Number(n || 0).toLocaleString();
const time = (value) =>
  value
    ? new Date(value).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      })
    : "—";
function download(data) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = `fynd-evidence-${new Date().toISOString().replaceAll(":", "-")}.json`;
  a.click();
  URL.revokeObjectURL(url);
}
function Badge({ children, tone = "" }) {
  return <span className={"badge " + tone}>{children}</span>;
}
function Empty({ icon: Icon = Database, title, children }) {
  return (
    <div className="empty">
      <Icon size={30} />
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}

export function App() {
  return (
    <AuthGate>
      {({ admin, logout }) => (
        <OperationsConsole admin={admin} logout={logout} />
      )}
    </AuthGate>
  );
}

function OperationsConsole({ admin, logout }) {
  const [signingOut, setSigningOut] = useState(false),
    [logoutError, setLogoutError] = useState("");
  async function signOut() {
    setSigningOut(true);
    setLogoutError("");
    try {
      await logout();
    } catch (e) {
      setLogoutError(e.message);
    } finally {
      setSigningOut(false);
    }
  }
  const [view, setView] = useState(
    location.hash === "#tests" ? "tests" : "dashboard",
  );
  const [data, setData] = useState(null),
    [health, setHealth] = useState(null),
    [events, setEvents] = useState([]),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(""),
    [notice, setNotice] = useState("");
  const [query, setQuery] = useState(""),
    [selected, setSelected] = useState(null),
    [filter, setFilter] = useState("all");
  const [catalog, setCatalog] = useState(null),
    [results, setResults] = useState({}),
    [running, setRunning] = useState(""),
    [activeCase, setActiveCase] = useState("retry"),
    [group, setGroup] = useState("All cases");
  const [suite, setSuite] = useState(false),
    [catalogError, setCatalogError] = useState("");
  const cancel = useRef(false),
    fetching = useRef(false),
    mounted = useRef(true);
  async function refresh() {
    if (fetching.current) return;
    fetching.current = true;
    try {
      const [snapshot, h, e] = await Promise.all([
        api("/dashboard", { signal: AbortSignal.timeout(8000) }),
        api("/health", { signal: AbortSignal.timeout(8000) }),
        api("/events?limit=30", { signal: AbortSignal.timeout(8000) }),
      ]);
      if (mounted.current) {
        setData(snapshot);
        setHealth(h);
        setEvents(e);
        setError("");
      }
    } catch (e) {
      if (mounted.current) setError(e.message);
    } finally {
      fetching.current = false;
    }
  }
  async function loadCatalog() {
    try {
      setCatalog(
        await api("/verification/cases", { signal: AbortSignal.timeout(8000) }),
      );
      setCatalogError("");
    } catch (e) {
      setCatalogError(e.message);
    }
  }
  useEffect(() => {
    mounted.current = true;
    refresh();
    loadCatalog();
    const id = setInterval(refresh, 2500);
    return () => {
      mounted.current = false;
      clearInterval(id);
      cancel.current = true;
    };
  }, []);
  useEffect(() => {
    function change() {
      setView(location.hash === "#tests" ? "tests" : "dashboard");
    }
    window.addEventListener("hashchange", change);
    return () => window.removeEventListener("hashchange", change);
  }, []);
  useEffect(() => {
    if (!selected) return;
    const previous = document.activeElement;
    const oldOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    function trap(e) {
      if (e.key === "Escape") {
        setSelected(null);
        return;
      }
      if (e.key !== "Tab") return;
      const buttons = document.querySelectorAll('[role="dialog"] button');
      const first = buttons[0],
        last = buttons[buttons.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last?.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first?.focus();
      }
    }
    document.addEventListener("keydown", trap);
    return () => {
      document.body.style.overflow = oldOverflow;
      document.removeEventListener("keydown", trap);
      previous?.focus();
    };
  }, [selected]);
  function navigate(next) {
    setView(next);
    location.hash = next === "tests" ? "tests" : "dashboard";
    setSelected(null);
  }
  async function action(kind) {
    setBusy(kind);
    setNotice("");
    try {
      await api("/mock/" + kind, {
        method: "POST",
        body:
          kind === "start"
            ? JSON.stringify({ events_per_second: 5 })
            : undefined,
      });
      setNotice(
        kind === "start"
          ? "Mock traffic started at 5 events per second."
          : "Mock traffic paused.",
      );
      await refresh();
    } catch (e) {
      setNotice(e.message);
    } finally {
      setBusy("");
    }
  }
  async function runCase(id, replayBeforeNext = false) {
    setRunning(id);
    setActiveCase(id);
    setResults((old) => ({ ...old, [id]: { status: "running" } }));
    try {
      const result = await post("/verification/run/" + id);
      setResults((old) => ({ ...old, [id]: result }));
      if (replayBeforeNext && result.steps?.length) {
        // Let the actual snapshot replay finish before Run all opens the next case.
        await new Promise((resolve) =>
          setTimeout(resolve, result.steps.length * 1150),
        );
      }
    } catch (e) {
      setResults((old) => ({
        ...old,
        [id]: {
          case_id: id,
          status: "error",
          output: e.message,
          completed_at: new Date().toISOString(),
        },
      }));
    } finally {
      setRunning("");
    }
  }
  async function runAll() {
    setSuite(true);
    cancel.current = false;
    setResults({});
    try {
      for (const c of catalog.cases) {
        if (cancel.current) break;
        await runCase(c.id, true);
      }
    } finally {
      setSuite(false);
    }
  }
  const stores = data?.stores || [],
    totals = Object.fromEntries(
      ["total", ...STATUS].map((k) => [
        k,
        stores.reduce((sum, s) => sum + s[k], 0),
      ]),
    );
  const alerts = data?.summary?.anomalies || [];
  const visible = stores.filter(
    (s) =>
      (s.store_id + " " + (STORE_NAMES[s.store_id] || ""))
        .toLowerCase()
        .includes(query.toLowerCase()) &&
      (filter === "all" || alerts.some((a) => a.store_id === s.store_id)),
  );
  const passed = Object.values(results).filter(
      (r) => r.status === "passed",
    ).length,
    failed = Object.values(results).filter((r) =>
      ["failed", "error"].includes(r.status),
    ).length;
  const cases = catalog?.cases || [],
    current = cases.find((c) => c.id === activeCase),
    result = results[activeCase];
  const groups = ["All cases", ...new Set(cases.map((c) => c.group))];
  const allBusy = Boolean(running || suite);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a
          className="brand"
          href="#dashboard"
          onClick={() => navigate("dashboard")}
        >
          <span className="brand-icon">
            <Zap size={22} fill="currentColor" />
          </span>
          fynd<span className="brand-dot">.</span>
          <span className="brand-sub">OPERATIONS</span>
        </a>
        <div className="workspace">
          <div className="workspace-icon">
            <Store size={18} />
          </div>
          <div>
            <strong>Mock Store Network</strong>
            <span>Candidate workspace</span>
          </div>
          <ChevronDown size={14} />
        </div>
        <p className="nav-label">WORKSPACE</p>
        <nav aria-label="Main navigation">
          <button
            className={view === "dashboard" ? "active" : ""}
            onClick={() => navigate("dashboard")}
          >
            <LayoutDashboard size={18} /> Overview{" "}
            <span className="nav-count">{stores.length || "—"}</span>
          </button>
          <button
            className={view === "tests" ? "active" : ""}
            onClick={() => navigate("tests")}
          >
            <FlaskConical size={18} /> Verification lab{" "}
            <span className="new-label">TEST</span>
          </button>
        </nav>
        <div className="sidebar-note">
          <div className="small-icon">
            <ShieldCheck size={19} />
          </div>
          <strong>Built on durable truth</strong>
          <p>Every count comes directly from shared order state.</p>
          <div>
            <span
              className={
                "dot " + (health?.database && !error ? "green" : "amber")
              }
            />{" "}
            {error
              ? "Connection unavailable"
              : health?.database
                ? "Authoritative database"
                : "Connecting to backend"}
          </div>
        </div>
        <div className="profile">
          <span className="avatar">AT</span>
          <div>
            <strong>Albin Thomas</strong>
            <span>Round 2 · JioMart OS</span>
          </div>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            Workspace <ChevronRight size={14} />
            <strong>
              {view === "dashboard"
                ? "Operations overview"
                : "Verification lab"}
            </strong>
          </div>
          <div className="topbar-right">
            <button
              className="button sign-out"
              onClick={signOut}
              disabled={signingOut}
            >
              {signingOut ? "Signing out…" : "Sign out"} · {admin.username}
            </button>
            <span className="synthetic">
              <span className="dot purple" /> Synthetic data
            </span>
            <span className="topbar-divider" />
            <span className="avatar small">AT</span>
          </div>
        </header>
        <main>
          {logoutError && (
            <div className="banner error" role="alert">
              {logoutError}
            </div>
          )}
          <div className="page-heading">
            <div>
              <div className="eyebrow">
                {view === "dashboard"
                  ? "YOUR NETWORK, AT A GLANCE"
                  : "FROM REQUIREMENTS TO EVIDENCE"}
              </div>
              <h1>
                {view === "dashboard"
                  ? "Operations overview"
                  : "Verification lab"}
                <span className="title-dot">.</span>
              </h1>
              <p>
                {view === "dashboard"
                  ? "A live view of every store. Every order. Every exception."
                  : "Run the candidate brief against the real backend. Inspect every result."}
              </p>
            </div>
            <div className="heading-actions">
              {view === "dashboard" ? (
                <>
                  <button
                    className="button"
                    onClick={refresh}
                    disabled={!!busy}
                  >
                    <RefreshCw size={15} />
                    Refresh
                  </button>
                  <button
                    className="button primary"
                    onClick={() => navigate("tests")}
                  >
                    <FlaskConical size={16} />
                    Enter test mode
                    <ArrowUpRight size={15} />
                  </button>
                </>
              ) : (
                <>
                  <button
                    className="button"
                    disabled={
                      !Object.values(results).some((r) => r.completed_at)
                    }
                    onClick={() =>
                      download({
                        generated_at: new Date().toISOString(),
                        scope:
                          "Isolated synthetic verification against backend code",
                        cases,
                        results,
                      })
                    }
                  >
                    <ArrowDownToLine size={16} />
                    Export evidence
                  </button>
                  <button
                    className="button primary"
                    disabled={allBusy || !catalog?.enabled}
                    onClick={runAll}
                  >
                    {suite ? (
                      <LoaderCircle className="spin" size={16} />
                    ) : (
                      <Play size={15} fill="currentColor" />
                    )}
                    Run all cases
                  </button>
                </>
              )}
            </div>
          </div>

          {error && (
            <div role="alert" className="banner error">
              <AlertTriangle size={19} />
              <div>
                <strong>
                  {data
                    ? "Live updates interrupted — showing stale data"
                    : "Backend unavailable"}
                </strong>
                <p>
                  {error}
                  {data
                    ? ` Last successful snapshot: ${time(data.generated_at)}.`
                    : ""}
                </p>
              </div>
              <button className="text-button" onClick={refresh}>
                Retry
              </button>
            </div>
          )}

          {view === "dashboard" ? (
            <>
              <section className="traffic-bar traffic-top">
                <span className="traffic-icon">
                  <Radio size={22} />
                </span>
                <div>
                  <strong>Mock traffic</strong>
                  <p>
                    Stream synthetic orders across your stores at 5 events /
                    second.
                  </p>
                </div>
                <div className="traffic-actions">
                  <button
                    className="button dark"
                    disabled={!!busy || !!error || !data}
                    onClick={() =>
                      action(health?.mock_enabled ? "stop" : "start")
                    }
                  >
                    {busy ? (
                      <LoaderCircle className="spin" size={15} />
                    ) : health?.mock_enabled ? (
                      <Pause size={15} />
                    ) : (
                      <Play size={15} />
                    )}
                    {health?.mock_enabled
                      ? "Pause traffic"
                      : "Start mock traffic"}
                  </button>
                </div>
              </section>
              <div className="status-strip">
                <div>
                  <span className={"live-pill " + (error ? "stale" : "")}>
                    <span className={"dot " + (error ? "amber" : "green")} />
                    {error ? "STALE" : data ? "LIVE" : "CONNECTING"}
                  </span>
                  <span>Network snapshot</span>
                  <span className="muted">
                    Updated {time(data?.generated_at)}
                  </span>
                </div>
                <span className="muted">
                  <Database size={13} /> Shared authoritative state
                </span>
              </div>
              <section className="metrics" aria-label="Network metrics">
                {[
                  {
                    label: "Total retained orders",
                    value: totals.total,
                    icon: Package,
                    meta: `Across ${stores.length} mock stores`,
                    cls: "violet",
                  },
                  {
                    label: "In fulfilment",
                    value:
                      totals.open + totals.packed + totals.out_for_delivery,
                    icon: Box,
                    meta: `${num(totals.open)} open · ${num(totals.packed)} packed`,
                    cls: "blue",
                  },
                  {
                    label: "Delivered",
                    value: totals.delivered,
                    icon: CircleCheck,
                    meta: totals.total
                      ? `${Math.round((totals.delivered / totals.total) * 100)}% of retained orders`
                      : "No orders yet",
                    cls: "green",
                  },
                  {
                    label: "Needs attention",
                    value: alerts.length,
                    icon: Activity,
                    meta: `${new Set(alerts.map((a) => a.store_id)).size} stores with exceptions`,
                    cls: "orange",
                  },
                ].map((m) => (
                  <article className="metric" key={m.label}>
                    <div>
                      <span>{m.label}</span>
                      <span className={"metric-icon " + m.cls}>
                        <m.icon size={19} />
                      </span>
                    </div>
                    <strong>{data ? num(m.value) : "—"}</strong>
                    <p>
                      <span className={"tiny-line " + m.cls} />
                      {m.meta}
                    </p>
                  </article>
                ))}
              </section>
              <div className="dashboard-grid">
                <section className="panel stores-panel">
                  <div className="panel-heading">
                    <div>
                      <h2>
                        Store performance{" "}
                        <span className="count">{stores.length}</span>
                      </h2>
                      <p>Current order state across your network</p>
                    </div>
                    <div className="segmented">
                      <button
                        className={filter === "all" ? "active" : ""}
                        onClick={() => setFilter("all")}
                      >
                        All stores
                      </button>
                      <button
                        className={filter === "attention" ? "active" : ""}
                        onClick={() => setFilter("attention")}
                      >
                        Needs attention
                      </button>
                    </div>
                  </div>
                  <div className="table-tools">
                    <label className="search">
                      <Search size={16} />
                      <input
                        placeholder="Search stores…"
                        aria-label="Search stores"
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                      />
                    </label>
                    <span className="muted">{visible.length} stores</span>
                  </div>
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>STORE</th>
                          <th>TOTAL</th>
                          <th>OPEN</th>
                          <th>PACKED</th>
                          <th>ON THE ROAD</th>
                          <th>DELIVERED</th>
                          <th />
                        </tr>
                      </thead>
                      <tbody>
                        {visible.map((s, i) => (
                          <tr key={s.store_id} onClick={() => setSelected(s)}>
                            <td>
                              <button
                                className="store-cell"
                                onClick={() => setSelected(s)}
                              >
                                <span className={"store-icon store-" + (i % 3)}>
                                  <Store size={18} />
                                </span>
                                <span>
                                  <strong>
                                    {STORE_NAMES[s.store_id] || s.store_id}
                                  </strong>
                                  <small>{s.store_id}</small>
                                </span>
                              </button>
                            </td>
                            <td className="total-cell">{num(s.total)}</td>
                            {[
                              "open",
                              "packed",
                              "out_for_delivery",
                              "delivered",
                            ].map((k) => (
                              <td key={k}>
                                <span className={"number-badge " + k}>
                                  {num(s[k])}
                                </span>
                              </td>
                            ))}
                            <td>
                              <ChevronRight size={16} />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {!visible.length && (
                    <Empty
                      icon={Store}
                      title={
                        stores.length
                          ? "No matching stores"
                          : data
                            ? "Your network is ready"
                            : "Waiting for your backend"
                      }
                    >
                      {stores.length
                        ? "Try a different search or filter."
                        : data
                          ? "Start mock traffic above to populate your stores."
                          : "Connect the backend to see live orders and store metrics."}
                    </Empty>
                  )}
                  <div className="table-footer">
                    <ShieldCheck size={14} /> One retained order. One store. One
                    current status.
                    <span>Includes returned orders in totals</span>
                  </div>
                </section>
                <section className="panel flow-panel">
                  <div className="panel-heading">
                    <div>
                      <h2>Order distribution</h2>
                      <p>All retained orders by status</p>
                    </div>
                    <Box size={18} className="muted" />
                  </div>
                  <div
                    className="donut"
                    style={{
                      background: totals.total
                        ? `conic-gradient(${STATUS.map((s, i) => `var(--${s}) ${(STATUS.slice(0, i).reduce((n, k) => n + totals[k], 0) / totals.total) * 100}% ${(STATUS.slice(0, i + 1).reduce((n, k) => n + totals[k], 0) / totals.total) * 100}%`).join(",")})`
                        : "var(--line)",
                    }}
                  >
                    <div>
                      <strong>{data ? num(totals.total) : "—"}</strong>
                      <span>Total orders</span>
                    </div>
                  </div>
                  <div className="legend">
                    {STATUS.map((s) => (
                      <div key={s}>
                        <span>
                          <i style={{ background: `var(--${s})` }} />
                          {LABELS[s]}
                        </span>
                        <strong>{num(totals[s])}</strong>
                        <small>
                          {totals.total
                            ? Math.round((totals[s] / totals.total) * 100)
                            : 0}
                          %
                        </small>
                      </div>
                    ))}
                  </div>
                </section>
              </div>
              <div className="lower-grid">
                <section className="panel">
                  <div className="panel-heading">
                    <div>
                      <h2>
                        <span className="inline-icon orange">
                          <Zap size={17} />
                        </span>
                        Operations brief{" "}
                        <span className="count">{alerts.length}</span>
                      </h2>
                      <p>Deterministic rules. Actionable exceptions.</p>
                    </div>
                    <Badge tone="neutral">RULE ENGINE</Badge>
                  </div>
                  <div className="alerts">
                    {alerts.length ? (
                      alerts.map((a, i) => (
                        <article className="alert-card" key={i}>
                          <span
                            className={
                              "alert-icon " +
                              (a.severity === "high" ? "red" : "orange")
                            }
                          >
                            {a.type === "delivery_delay" ? (
                              <Truck size={19} />
                            ) : (
                              <Activity size={19} />
                            )}
                          </span>
                          <div>
                            <div className="alert-title">
                              <strong>{a.type.replaceAll("_", " ")}</strong>
                              <Badge
                                tone={
                                  a.severity === "high" ? "danger" : "warning"
                                }
                              >
                                {a.severity === "high"
                                  ? "High priority"
                                  : "Review"}
                              </Badge>
                            </div>
                            <small>
                              {STORE_NAMES[a.store_id] || a.store_id} ·{" "}
                              {a.store_id}
                            </small>
                            <p>{a.recommended_action}</p>
                          </div>
                        </article>
                      ))
                    ) : (
                      <Empty
                        icon={ShieldCheck}
                        title={
                          data
                            ? "No thresholds exceeded"
                            : "Awaiting store data"
                        }
                      >
                        Backlog, delivery delays and status imbalance appear
                        here.
                      </Empty>
                    )}
                  </div>
                </section>
                <section className="panel">
                  <div className="panel-heading">
                    <div>
                      <h2>
                        Event activity{" "}
                        <span className="pulse-icon">
                          <Radio size={15} />
                        </span>
                      </h2>
                      <p>Latest persisted first-seen decisions</p>
                    </div>
                    <Badge tone="neutral">{events.length} RECENT</Badge>
                  </div>
                  <div className="event-list">
                    {events.slice(0, 5).map((e, i) => (
                      <div className="event" key={e.event_id}>
                        <span
                          className={
                            "event-dot " +
                            (e.outcome === "applied" ? "green" : "orange")
                          }
                        >
                          <Check size={13} />
                        </span>
                        <div>
                          <strong>
                            {LABELS[e.status]} <span>· {e.store_id}</span>
                          </strong>
                          <small title={e.order_id}>{e.order_id}</small>
                        </div>
                        <div className="event-meta">
                          <Badge
                            tone={
                              e.outcome === "applied" ? "success" : "warning"
                            }
                          >
                            {e.outcome}
                          </Badge>
                          <small>
                            v{e.version} · {time(e.occurred_at)}
                          </small>
                        </div>
                      </div>
                    ))}
                    {!events.length && (
                      <Empty icon={Activity} title="No events yet">
                        Start mock traffic to see decisions arrive.
                      </Empty>
                    )}
                  </div>
                </section>
              </div>
              {notice && (
                <div className="notice" role="status">
                  {notice}
                  <button
                    aria-label="Dismiss message"
                    onClick={() => setNotice("")}
                  >
                    <X size={14} />
                  </button>
                </div>
              )}
            </>
          ) : (
            <>
              <div className="lab-intro">
                <div className="lab-icon">
                  <ShieldCheck size={25} />
                </div>
                <div>
                  <strong>Real execution. Inspectable proof.</strong>
                  <p>
                    Each case runs the project’s backend tests in an isolated
                    database. Concurrency and recovery cases create independent
                    connections or dedicated processes.
                  </p>
                </div>
                <Badge tone="purple">CANDIDATE BRIEF · ROUND 2</Badge>
              </div>
              {catalogError && (
                <div className="banner error" role="alert">
                  <AlertTriangle size={18} />
                  <div>
                    <strong>Cannot load verification cases</strong>
                    <p>{catalogError}</p>
                  </div>
                  <button className="button" onClick={loadCatalog}>
                    Retry
                  </button>
                </div>
              )}
              {catalog && !catalog.enabled && (
                <div className="banner error">
                  <AlertTriangle size={18} />
                  <div>
                    <strong>Verification is disabled</strong>
                    <p>
                      Set VERIFICATION_ENABLED=true on the backend to enable the
                      isolated runner.
                    </p>
                  </div>
                  <button className="button" onClick={loadCatalog}>
                    Refresh
                  </button>
                </div>
              )}
              <div className="lab-stats">
                <div>
                  <span className="lab-stat-icon purple">
                    <FlaskConical size={20} />
                  </span>
                  <div>
                    <strong>{cases.length || "—"}</strong>
                    <span>Verification cases</span>
                  </div>
                </div>
                <div>
                  <span className="lab-stat-icon green">
                    <CheckCheck size={21} />
                  </span>
                  <div>
                    <strong>{passed}</strong>
                    <span>Passed</span>
                  </div>
                </div>
                <div>
                  <span className="lab-stat-icon red">
                    <AlertTriangle size={20} />
                  </span>
                  <div>
                    <strong>{failed}</strong>
                    <span>Failed / unavailable</span>
                  </div>
                </div>
                <div>
                  <span className="lab-stat-icon neutral">
                    <Clock3 size={20} />
                  </span>
                  <div>
                    <strong>
                      {Math.max(
                        0,
                        cases.length - passed - failed - (running ? 1 : 0),
                      )}
                    </strong>
                    <span>Not run</span>
                  </div>
                </div>
              </div>
              {suite && (
                <div className="suite-progress" role="status">
                  <LoaderCircle size={16} className="spin" />
                  <span>
                    {results[running]?.steps?.length ? "Replaying" : "Running"}{" "}
                    {cases.find((c) => c.id === running)?.title ||
                      "verification"}
                    …
                  </span>
                  <strong>
                    {passed + failed} / {cases.length}
                  </strong>
                  <button
                    className="text-button"
                    onClick={() => {
                      cancel.current = true;
                    }}
                  >
                    Stop after current case
                  </button>
                </div>
              )}
              <div className="lab-grid">
                <section className="panel case-panel">
                  <div className="panel-heading">
                    <div>
                      <h2>Test cases</h2>
                      <p>Run everything, or explore one case at a time.</p>
                    </div>
                    <select
                      aria-label="Filter test group"
                      value={group}
                      onChange={(e) => setGroup(e.target.value)}
                    >
                      {groups.map((g) => (
                        <option key={g}>{g}</option>
                      ))}
                    </select>
                  </div>
                  <div className="case-list">
                    {cases
                      .filter((c) => group === "All cases" || c.group === group)
                      .map((c, i) => {
                        const r = results[c.id];
                        return (
                          <article
                            className={
                              "case " + (activeCase === c.id ? "selected" : "")
                            }
                            key={c.id}
                          >
                            <button
                              className="case-info"
                              disabled={suite}
                              onClick={() => setActiveCase(c.id)}
                            >
                              <span
                                className={
                                  "case-indicator " + (r?.status || "")
                                }
                              >
                                {r?.status === "running" ? (
                                  <LoaderCircle size={17} className="spin" />
                                ) : r?.status === "passed" ? (
                                  <Check size={17} />
                                ) : ["failed", "error"].includes(r?.status) ? (
                                  <X size={16} />
                                ) : (
                                  <span>
                                    {String(cases.indexOf(c) + 1).padStart(
                                      2,
                                      "0",
                                    )}
                                  </span>
                                )}
                              </span>
                              <span>
                                <small>{c.group}</small>
                                <strong>{c.title}</strong>
                                <p>{c.description}</p>
                                <span
                                  className={"case-status " + (r?.status || "")}
                                >
                                  {r?.status === "passed"
                                    ? "Passed"
                                    : r?.status === "failed"
                                      ? "Failed"
                                      : r?.status === "error"
                                        ? "Execution unavailable"
                                        : r?.status === "running"
                                          ? "Running…"
                                          : "Not run"}
                                  {r?.duration_ms
                                    ? ` · ${(r.duration_ms / 1000).toFixed(2)}s`
                                    : ""}
                                </span>
                              </span>
                            </button>
                            <button
                              className="run-button"
                              aria-label={"Run " + c.title}
                              disabled={allBusy || !catalog?.enabled}
                              onClick={() => runCase(c.id)}
                            >
                              {r?.status === "running" ? (
                                <LoaderCircle className="spin" size={16} />
                              ) : r ? (
                                <RotateCcw size={16} />
                              ) : (
                                <Play size={16} />
                              )}
                            </button>
                          </article>
                        );
                      })}
                  </div>
                  {!cases.length && (
                    <Empty
                      icon={FlaskConical}
                      title="Waiting for the test catalog"
                    >
                      Connect the backend to load executable cases.
                    </Empty>
                  )}
                </section>
                <div className="evidence-column">
                  <TestStoreDashboard
                    key={activeCase}
                    result={result}
                    title={current?.title}
                    locked={suite}
                  />
                  <section className="coverage-card">
                    <h3>
                      <ShieldCheck size={17} /> What this proves
                    </h3>
                    <p>
                      Identity, version authority, store ownership, atomic
                      writes and recovery are checked against the same backend
                      implementation as the dashboard.
                    </p>
                    <ul>
                      <li>
                        <Check size={14} />
                        Exception injection and real kills are separate cases
                      </li>
                      <li>
                        <Check size={14} />
                        HTTP test launches two writers and a third reader
                      </li>
                      <li>
                        <Check size={14} />
                        Dashboard & SSE freshness checked against 5 seconds
                      </li>
                    </ul>
                    <div>
                      Isolated functional evidence, not a production load test.
                      A race test does not exhaust every interleaving.
                    </div>
                  </section>
                </div>
              </div>
            </>
          )}
          <footer>
            <span>
              <span className="brand-mini">fynd.</span> Operations Console{" "}
              <span className="footer-divider">/</span> Albin Thomas
            </span>
            <span>
              <span
                className={
                  "dot " + (health?.database && !error ? "green" : "amber")
                }
              />
              {error
                ? "Backend unreachable"
                : health?.database
                  ? "Database connected"
                  : "Awaiting connection"}
              <span className="footer-divider">·</span>
              {health?.redis ? "Cache connected" : "Cache optional"}
            </span>
          </footer>
        </main>
      </div>
      {selected && (
        <div className="modal-backdrop" onClick={() => setSelected(null)}>
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="store-title"
            className="store-modal"
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === "Escape") setSelected(null);
            }}
          >
            <button
              className="modal-close"
              aria-label="Close store details"
              autoFocus
              onClick={() => setSelected(null)}
            >
              <X size={20} />
            </button>
            <span className="store-icon">
              <Store size={25} />
            </span>
            <h2 id="store-title">
              {STORE_NAMES[selected.store_id] || selected.store_id}
            </h2>
            <p>
              {selected.store_id} · Snapshot {time(data?.generated_at)}
            </p>
            <MockStoreSummary store={selected} />
            <div className="store-total">
              {num(selected.total)}
              <span>retained orders</span>
            </div>
            {STATUS.map((k) => (
              <div className="detail-row" key={k}>
                <span>
                  <i style={{ background: `var(--${k})` }} />
                  {LABELS[k]}
                </span>
                <strong>{num(selected[k])}</strong>
              </div>
            ))}
            <div className="detail-row">
              <span>Delayed deliveries</span>
              <strong>{num(selected.delayed_deliveries)}</strong>
            </div>
            <p className="modal-note">
              Total = open + packed + out for delivery + delivered + returned.
            </p>
            <button
              className="button primary"
              onClick={() => setSelected(null)}
            >
              Done
            </button>
          </section>
        </div>
      )}
    </div>
  );
}
if (document.getElementById("root"))
  createRoot(document.getElementById("root")).render(<App />);
