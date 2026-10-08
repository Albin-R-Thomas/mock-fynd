import React, { useEffect, useRef, useState } from "react";
import TestDetails from "./TestDetails.jsx";
import {
  Store,
  Package,
  Box,
  CheckCheck,
  Activity,
  Play,
  Pause,
  ChevronRight,
  RotateCcw,
  Terminal,
  ShieldCheck,
} from "lucide-react";

const statuses = [
  "open",
  "packed",
  "out_for_delivery",
  "delivered",
  "returned",
];
const labels = {
  open: "Open",
  packed: "Packed",
  out_for_delivery: "On the road",
  delivered: "Delivered",
  returned: "Returned",
};
const total = (stores, key) => stores.reduce((n, s) => n + (s[key] || 0), 0);

export default function TestStoreDashboard({ result, title, locked = false }) {
  const steps = result?.steps || [];
  const logRef = useRef(null);
  const followLog = useRef(true);
  const [index, setIndex] = useState(0),
    [playing, setPlaying] = useState(true);
  useEffect(() => {
    setIndex(0);
    setPlaying(true);
    followLog.current = true;
  }, [result]);
  useEffect(() => {
    if (!playing || index >= steps.length - 1) return;
    const timer = setTimeout(() => setIndex((i) => i + 1), 1100);
    return () => clearTimeout(timer);
  }, [playing, index, steps.length]);
  useEffect(() => {
    if (followLog.current && logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [index, result]);
  const shown = steps.slice(0, index + 1);
  const snapshots = shown.filter((step) => Array.isArray(step.stores));
  const snapshot = snapshots.at(-1);
  const stores = snapshot?.stores || [];
  const orders = snapshot?.orders || [];
  const step = steps[index];
  const unavailable =
    shown.filter((s) => s.kind !== "assertion").at(-1)?.available === false;
  const previous = snapshots.at(-2);
  const unchanged = Boolean(
    snapshot &&
    previous &&
    JSON.stringify(previous.stores) === JSON.stringify(snapshot.stores),
  );
  const seen = shown.reduce((n, s) => n + (s.decisions?.length || 0), 0);
  const busy = result?.status === "running";
  const finished = Boolean(steps.length && index === steps.length - 1);
  return (
    <section className="test-store-view" aria-label="Test store dashboard">
      {unavailable && (
        <div className="banner error" role="alert">
          Store reads are unavailable. Displayed counts are the last successful
          observation.
        </div>
      )}
      <div className="test-store-heading">
        <div>
          <span className="eyebrow">TEST STORE NETWORK</span>
          <h2>{title || "Store preview"}</h2>
          <p>
            {busy
              ? "Executing the case and recording store changes…"
              : steps.length
                ? "Replay of actual backend snapshots · isolated test data"
                : "Run a case to watch its events change the store dashboard."}
          </p>
        </div>
        <span
          className={
            "badge " +
            (busy
              ? "purple"
              : result?.status === "passed"
                ? "success"
                : "neutral")
          }
        >
          {busy
            ? "EXECUTING"
            : finished
              ? "REPLAY COMPLETE"
              : steps.length
                ? "RECORDED EXECUTION"
                : "READY"}
        </span>
      </div>
      <div className="terminal short-terminal">
        <div>
          <Terminal size={14} />
          <span>Event log</span>
          <span className="log-note">{shown.length} recorded steps</span>
        </div>
        <pre
          ref={logRef}
          aria-live="polite"
          aria-label="Test event log"
          tabIndex={0}
          onScroll={(e) => {
            const el = e.currentTarget;
            followLog.current =
              el.scrollHeight - el.scrollTop - el.clientHeight < 24;
          }}
        >
          {steps.length
            ? shown
                .map(
                  (s, i) => `${String(i + 1).padStart(2, "0")}  ${s.message}`,
                )
                .join("\n")
            : busy
              ? "Running the backend case…"
              : result?.status === "error"
                ? result.output
                : "Ready. Run a case to follow its events here."}
        </pre>
      </div>
      <TestDetails steps={shown} busy={busy} />
      <div className="metrics test-metrics">
        {[
          [
            "Retained orders",
            snapshot ? total(stores, "total") : "—",
            Package,
            "violet",
          ],
          ["Open", snapshot ? total(stores, "open") : "—", Box, "blue"],
          [
            "Delivered",
            snapshot ? total(stores, "delivered") : "—",
            CheckCheck,
            "green",
          ],
          ["Event attempts", seen, Activity, "orange"],
        ].map(([label, value, Icon, color]) => (
          <article className="metric" key={label}>
            <div>
              <span>{label}</span>
              <span className={"metric-icon " + color}>
                <Icon size={17} />
              </span>
            </div>
            <strong aria-label={label + " count"}>{value}</strong>
            <p>
              {label === "Event attempts"
                ? "Includes retries and rejections"
                : "Authoritative store state"}
            </p>
          </article>
        ))}
      </div>
      <section className="panel test-performance">
        <div className="panel-heading">
          <div>
            <h2>
              Store performance <span className="count">{stores.length}</span>
            </h2>
            <p>Same store view. One event at a time.</p>
          </div>
          {snapshot && (
            <span className={"badge " + (unchanged ? "neutral" : "success")}>
              {unchanged ? "COUNTS UNCHANGED" : "STORE SNAPSHOT"}
            </span>
          )}
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>STORE</th>
                <th>TOTAL</th>
                {statuses.map((s) => (
                  <th key={s}>{labels[s].toUpperCase()}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {stores.map((s) => (
                <tr key={s.store_id}>
                  <td>
                    <span className="store-cell">
                      <span className="store-icon">
                        <Store size={17} />
                      </span>
                      <span>
                        <strong>Mock store {s.store_id}</strong>
                        <small>{s.store_id}</small>
                      </span>
                    </span>
                  </td>
                  <td className="total-cell">{s.total}</td>
                  {statuses.map((k) => (
                    <td key={k}>
                      <span className={"number-badge " + k}>{s[k]}</span>
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!stores.length && (
          <div className="empty">
            <Store size={26} />
            <h3>
              {snapshot
                ? "No retained orders yet"
                : "Waiting for a store snapshot"}
            </h3>
            <p>
              {snapshot
                ? "The first accepted event will appear here."
                : "Validation-only checks may reject input before creating any orders."}
            </p>
          </div>
        )}
        <div className="test-status-bars">
          {statuses.map((k) => (
            <div key={k}>
              <span>
                <i style={{ background: `var(--${k})` }} />
                {labels[k]}
              </span>
              <strong>{total(stores, k)}</strong>
              <div>
                <span
                  style={{
                    width: `${total(stores, "total") ? (total(stores, k) / total(stores, "total")) * 100 : 0}%`,
                    background: `var(--${k})`,
                  }}
                />
              </div>
            </div>
          ))}
        </div>
        <div className="table-footer">
          <ShieldCheck size={14} /> A retry adds an attempt, never an extra
          order.
        </div>
      </section>
      {!!orders.length && (
        <section className="test-orders">
          <h3>Current orders</h3>
          {orders.map((o) => (
            <div key={o.order_id}>
              <span className="test-order-id">{o.order_id}</span>
              <span>{o.store_id}</span>
              <span className={"number-badge " + o.status}>
                {labels[o.status]}
              </span>
              <strong>v{o.version}</strong>
            </div>
          ))}
        </section>
      )}
      {step && (
        <div
          className={
            "step-explanation " +
            (step.available === false ? "unavailable" : "")
          }
          role="status"
        >
          <span>
            STEP {index + 1} / {steps.length}
          </span>
          <strong>{step.message}</strong>
          {step.available === false && (
            <p>
              Store reads are unavailable. Any displayed counts are the last
              successful observation.
            </p>
          )}
        </div>
      )}
      {!!steps.length && (
        <div className="replay-controls">
          <button
            className="button"
            disabled={locked}
            onClick={() => {
              setIndex(0);
              setPlaying(true);
            }}
          >
            <RotateCcw size={14} />
            Replay
          </button>
          <button
            className="button"
            disabled={locked || finished}
            onClick={() => setPlaying(!playing)}
          >
            {playing ? <Pause size={14} /> : <Play size={14} />}{" "}
            {playing ? "Pause" : "Play"}
          </button>
          <button
            className="button"
            disabled={locked || index === 0}
            onClick={() => {
              setPlaying(false);
              setIndex((i) => i - 1);
            }}
          >
            Previous
          </button>
          <button
            className="button"
            disabled={locked || finished}
            onClick={() => {
              setPlaying(false);
              setIndex((i) => i + 1);
            }}
          >
            Next step
            <ChevronRight size={14} />
          </button>
        </div>
      )}
      {result?.output && result.status !== "error" && (
        <details className="raw-evidence">
          <summary>Full test report</summary>
          <pre>{result.output}</pre>
        </details>
      )}
    </section>
  );
}
