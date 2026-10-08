import React, { useState } from "react";
import { BookOpen, ChevronDown } from "lucide-react";

const outcomes = {
  applied: [
    "Accept the event and save the order’s new state.",
    "A valid newer version becomes the authoritative state. Updating an existing order must not create an extra order.",
  ],
  duplicate: [
    "Recognize an identical event that has already been received.",
    "Retries must be safe. The attempt appears in the log, but the stored order and store counts stay unchanged.",
  ],
  event_id_conflict: [
    "Reject a reused event ID whose payload has changed.",
    "An event ID must identify one immutable event. Preserving the original prevents a retry from rewriting history.",
  ],
  stale: [
    "Ignore an equal or older order version.",
    "Source versions decide which update wins. A late arrival must not overwrite newer state.",
  ],
  store_conflict: [
    "Reject an attempt to assign an existing order to another store.",
    "Each order belongs to one immutable store. This check takes priority over version ordering.",
  ],
};

function explain(step) {
  if (step.decisions?.length) {
    return step.decisions.map((decision) => ({
      label: decision.event_id || "Event",
      what:
        outcomes[decision.outcome]?.[0] ||
        `The backend returned ${decision.outcome}.`,
      why:
        outcomes[decision.outcome]?.[1] ||
        "Inspect the returned decision to understand how the request was handled.",
    }));
  }
  if (step.kind === "assertion") {
    const passed = step.message.endsWith(": passed");
    return [
      {
        what: passed
          ? "Check the observed behavior against this test’s assertions. The checks passed."
          : "Record the test’s assertion outcome shown in the log.",
        why: "Assertions compare actual results with the required behavior. A completed request alone is not proof that the case is correct.",
      },
    ];
  }
  if (step.kind === "failure") {
    return [
      {
        what: "Observe an interrupted write and read the state that remains in the database.",
        why: "Failures before commit should leave no partial changes; failures after commit must preserve the saved update. The recorded snapshot and subsequent retry show which state survived.",
      },
    ];
  }
  if (step.kind === "unavailable" || step.available === false) {
    return [
      {
        what: "Observe that the authoritative database cannot be read.",
        why: "The application must report unavailability instead of presenting an old snapshot as fresh data. Any retained dashboard values are marked stale.",
      },
    ];
  }
  if (step.kind === "rejected") {
    return [
      {
        what: "Send a request and record the API’s error response.",
        why: "Invalid input or unavailable storage must produce an explicit error, rather than being acknowledged as a successful update.",
      },
    ];
  }
  if (step.kind === "snapshot") {
    const count = (step.stores || []).reduce((n, store) => n + store.total, 0);
    return [
      {
        what: `Read the persisted store state: ${count} retained ${count === 1 ? "order" : "orders"} across ${(step.stores || []).length} stores.`,
        why: "Reading the database verifies what was actually saved. These observed counts populate the dashboard so you can compare state before and after each event.",
      },
    ];
  }
  return [
    {
      what: step.message,
      why: "Record this observation so it can be compared with the expected behavior and the surrounding store snapshots.",
    },
  ];
}

export default function TestDetails({ steps, busy }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="test-details">
      <button
        className="button test-details-toggle"
        aria-expanded={open}
        aria-controls="test-details-content"
        onClick={() => setOpen((value) => !value)}
      >
        <BookOpen size={14} />
        Test Details
        <ChevronDown size={13} className={open ? "expanded" : ""} />
      </button>
      {open && (
        <section
          id="test-details-content"
          className="test-details-content"
          aria-label="Test step explanations"
        >
          <h3>What’s happening and why</h3>
          <p>
            Follow the same step numbers as the terminal. Details appear as the
            recorded execution plays.
          </p>
          {!steps.length ? (
            <p>
              {busy
                ? "The backend is running the case. Its recorded actions will appear here when ready."
                : "Run a test to see its actions and the reason for each one."}
            </p>
          ) : (
            <ol>
              {steps.map((step, i) => (
                <li key={i}>
                  <span className="details-step">
                    STEP {String(i + 1).padStart(2, "0")}
                  </span>
                  {explain(step).map((item, j) => (
                    <div key={j}>
                      {item.label && (
                        <span className="details-event">{item.label}</span>
                      )}
                      <p>
                        <strong>What:</strong> {item.what}
                      </p>
                      <p>
                        <strong>Why:</strong> {item.why}
                      </p>
                    </div>
                  ))}
                </li>
              ))}
            </ol>
          )}
        </section>
      )}
    </div>
  );
}
