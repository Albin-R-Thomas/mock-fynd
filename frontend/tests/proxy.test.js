import { test } from "node:test";
import assert from "node:assert/strict";
import { allowed, proxy } from "../lib/proxy.js";
test("only allowlisted methods and routes can reach backend", () => {
  assert.equal(allowed("POST", "/verification/run/process-kill"), true);
  assert.equal(allowed("GET", "/dashboard"), true);
  for (const path of [
    "/events",
    "/../admin",
    "//evil.com",
    "/verification/run/a/b",
    "/mock/delete",
  ])
    assert.equal(allowed("POST", path), false);
  assert.equal(allowed("DELETE", "/dashboard"), false);
});
test("unconfigured deployment returns explicit unavailable", async () => {
  const prior = process.env.BACKEND_URL;
  delete process.env.BACKEND_URL;
  const res = {
    setHeader() {},
    status(code) {
      this.code = code;
      return this;
    },
    json(body) {
      this.body = body;
    },
  };
  try {
    await proxy({ method: "GET", url: "/api/dashboard" }, res);
    assert.equal(res.code, 503);
    assert.match(res.body.detail, /not connected/);
  } finally {
    if (prior) process.env.BACKEND_URL = prior;
  }
});

test("proxy preserves session cookies, CSRF and backend rejection", async () => {
  const previous = process.env.BACKEND_URL,
    originalFetch = global.fetch;
  process.env.BACKEND_URL = "https://backend.example";
  const headers = {};
  const res = {
    setHeader(k, v) {
      headers[k] = v;
    },
    status(code) {
      this.code = code;
      return this;
    },
    send(body) {
      this.body = body;
      return this;
    },
  };
  global.fetch = async (url, options) => {
    assert.equal(options.headers.Cookie, "fynd_session=opaque");
    assert.equal(options.headers["X-CSRF-Token"], "csrf-value");
    assert.equal(options.headers["X-Requested-With"], "fynd-console");
    assert.equal(options.headers["X-API-Key"], undefined);
    return new Response('{"detail":"Sign in to continue"}', {
      status: 401,
      headers: {
        "set-cookie":
          "fynd_session=; Max-Age=0; HttpOnly; Path=/; SameSite=Strict",
      },
    });
  };
  try {
    await proxy(
      {
        method: "POST",
        url: "/api/auth/logout",
        headers: {
          cookie: "analytics=private-value; fynd_session=opaque; unrelated=do-not-forward",
          "x-csrf-token": "csrf-value",
          "x-requested-with": "fynd-console",
        },
      },
      res,
    );
    assert.equal(res.code, 401);
    assert.match(headers["Set-Cookie"][0], /Max-Age=0/);
  } finally {
    global.fetch = originalFetch;
    if (previous === undefined) delete process.env.BACKEND_URL;
    else process.env.BACKEND_URL = previous;
  }
});
