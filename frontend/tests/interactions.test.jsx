// @vitest-environment jsdom
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, cleanup, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "../src/main.jsx";
import TestStoreDashboard from "../src/TestStoreDashboard.jsx";

const cases = [
  {
    id: "retry",
    title: "Identical retry",
    group: "Identity",
    description: "Retry changes no count.",
  },
  {
    id: "restart",
    title: "Service restart",
    group: "Recovery",
    description: "Restart preserves state.",
  },
];
let calls;
beforeEach(() => {
  location.hash = "";
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url, options = {}) => {
      calls.push({ url, method: options.method });
      let value;
      if (url.includes("/auth/session"))
        value = { username: "fynd", role: "admin", csrf_token: "test-csrf" };
      else if (url.includes("/verification/cases"))
        value = { enabled: true, cases };
      else if (url.includes("/verification/run/"))
        value = {
          case_id: url.split("/").at(-1),
          status: "passed",
          exit_code: 0,
          duration_ms: 30,
          completed_at: "2026-01-01T00:00:00Z",
          output: "ACTUAL ASSERTION OUTPUT",
        };
      else if (url.includes("/dashboard"))
        value = {
          generated_at: "2026-01-01T00:00:00Z",
          stores: [
            {
              store_id: "store-001",
              total: 5,
              open: 2,
              packed: 1,
              out_for_delivery: 0,
              delivered: 1,
              returned: 1,
              delayed_deliveries: 0,
            },
          ],
          summary: { anomalies: [] },
        };
      else if (url.includes("/health"))
        value = { database: true, redis: false, mock_enabled: false };
      else value = [];
      return { ok: true, json: async () => value };
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
describe("operations console", () => {
  it("requires sign-in before fetching store data and signs out cleanly", async () => {
    const original = fetch;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url, options = {}) => {
        if (url.endsWith("/auth/session"))
          return {
            ok: false,
            status: 401,
            json: async () => ({ detail: "Sign in to continue" }),
          };
        if (url.endsWith("/auth/login")) {
          const valid = JSON.parse(options.body).password === "fixture-password-not-a-real-login";
          return {
            ok: valid,
            status: valid ? 200 : 401,
            json: async () =>
              valid
                ? { username: "fynd", role: "admin", csrf_token: "login-csrf" }
                : { detail: "Invalid username or password" },
          };
        }
        if (url.endsWith("/auth/logout")) {
          expect(options.headers["X-CSRF-Token"]).toBe("login-csrf");
          return {
            ok: true,
            status: 200,
            json: async () => ({ signed_out: true }),
          };
        }
        return original(url, options);
      }),
    );
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Sign in" });
    expect(calls.some((c) => c.url.includes("/dashboard"))).toBe(false);
    expect(
      screen.queryByRole("button", { name: /Enter test mode/ }),
    ).toBeNull();
    await user.type(screen.getByLabelText("Username"), "fynd");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "Invalid username or password",
    );
    await user.clear(screen.getByLabelText("Password"));
    await user.type(screen.getByLabelText("Password"), "fixture-password-not-a-real-login");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await screen.findByRole("button", { name: /Enter test mode/ });
    await user.click(screen.getByRole("button", { name: /Sign out/ }));
    await screen.findByRole("button", { name: "Sign in" });
    expect(
      screen.queryByRole("button", { name: /Enter test mode/ }),
    ).toBeNull();
  });
  it("removes the dashboard when the session expires", async () => {
    render(<App />);
    await screen.findByRole("button", { name: /Enter test mode/ });
    act(() => window.dispatchEvent(new Event("fynd-session-expired")));
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeTruthy();
    expect(
      screen.queryByRole("region", { name: "Network metrics" }),
    ).toBeNull();
  });
  it("places mock traffic at the top and removes the scenario button", async () => {
    render(<App />);
    const traffic = await screen.findByRole("button", {
      name: "Start mock traffic",
    });
    const metrics = screen.getByRole("region", { name: "Network metrics" });
    expect(
      traffic.compareDocumentPosition(metrics) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Add scenario" })).toBeNull();
  });
  it("replays a retry in the log without increasing store or order counts", () => {
    vi.useFakeTimers();
    const store = {
      store_id: "s1",
      total: 1,
      open: 1,
      packed: 0,
      out_for_delivery: 0,
      delivered: 0,
      returned: 0,
    };
    render(
      <TestStoreDashboard
        title="Retry"
        result={{
          status: "passed",
          steps: [
            { kind: "snapshot", message: "Empty store", stores: [] },
            {
              kind: "event",
              message: "e1 applied",
              stores: [store],
              decisions: [{ outcome: "applied" }],
            },
            {
              kind: "event",
              message: "e1 duplicate",
              stores: [store],
              decisions: [{ outcome: "duplicate" }],
            },
          ],
        }}
      />,
    );
    expect(screen.getByLabelText("Retained orders count").textContent).toBe(
      "0",
    );
    act(() => vi.advanceTimersByTime(1100));
    expect(screen.getByLabelText("Retained orders count").textContent).toBe(
      "1",
    );
    expect(screen.getByLabelText("Event attempts count").textContent).toBe("1");
    act(() => vi.advanceTimersByTime(1100));
    expect(screen.getByLabelText("Retained orders count").textContent).toBe(
      "1",
    );
    expect(screen.getByLabelText("Open count").textContent).toBe("1");
    expect(screen.getByLabelText("Event attempts count").textContent).toBe("2");
    expect(screen.getByText("COUNTS UNCHANGED")).toBeTruthy();
    expect(screen.getByText(/03\s+e1 duplicate/)).toBeTruthy();
  });
  it("renders backend store counts, searches stores and opens complete status details", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(
      await screen.findByRole("button", { name: /Central Market/ }),
    );
    expect(screen.getByRole("dialog").textContent).toContain("Returned");
    await user.click(screen.getByRole("button", { name: "Done" }));
    await user.type(
      screen.getByRole("textbox", { name: "Search stores" }),
      "missing",
    );
    expect(screen.getByText("No matching stores")).toBeTruthy();
  });
  it("runs one case and displays actual backend evidence", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(
      await screen.findByRole("button", { name: /Enter test mode/ }),
    );
    await user.click(
      await screen.findByRole("button", { name: "Run Identical retry" }),
    );
    expect(
      await screen.findByText("ACTUAL ASSERTION OUTPUT", { exact: false }),
    ).toBeTruthy();
    expect(calls.filter((c) => c.method === "POST").map((c) => c.url)).toEqual([
      "/api/verification/run/retry",
    ]);
  });
  it("run all triggers every case sequentially and permits evidence export", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(
      await screen.findByRole("button", { name: /Enter test mode/ }),
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Run all cases" }).disabled,
      ).toBe(false),
    );
    await user.click(screen.getByRole("button", { name: "Run all cases" }));
    await waitFor(() =>
      expect(
        calls.filter((c) => c.method === "POST").map((c) => c.url),
      ).toEqual([
        "/api/verification/run/retry",
        "/api/verification/run/restart",
      ]),
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Export evidence" }).disabled,
      ).toBe(false),
    );
  });
  it("shows failures honestly instead of a passed result", async () => {
    const original = fetch;
    vi.stubGlobal(
      "fetch",
      vi.fn((url, options) =>
        url.includes("/verification/run/")
          ? Promise.resolve({
              ok: false,
              status: 503,
              json: async () => ({ detail: "Database offline" }),
            })
          : original(url, options),
      ),
    );
    const user = userEvent.setup();
    render(<App />);
    await user.click(
      await screen.findByRole("button", { name: /Enter test mode/ }),
    );
    await user.click(
      await screen.findByRole("button", { name: "Run Identical retry" }),
    );
    expect(await screen.findByText("Database offline")).toBeTruthy();
    expect(screen.getByText("Execution unavailable")).toBeTruthy();
  });
});
