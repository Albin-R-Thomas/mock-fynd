export function allowed(method, path) {
  return (
    (method === "GET" &&
      /^\/(dashboard|health|events|summary|verification\/cases|auth\/session)$/.test(
        path,
      )) ||
    (method === "POST" &&
      /^\/(mock\/(seed|start|stop)|verification\/run\/[a-z-]+|auth\/(login|logout))$/.test(
        path,
      ))
  );
}
export async function proxy(req, res) {
  res.setHeader("Cache-Control", "no-store");
  const incoming = new URL(req.url, "https://console.local");
  const path = incoming.pathname.replace(/^\/api/, "");
  if (!allowed(req.method, path))
    return res.status(404).json({ detail: "Route not available" });
  const backend = process.env.BACKEND_URL;
  if (!backend)
    return res.status(503).json({
      detail:
        "Backend is not connected. Set BACKEND_URL in Vercel, then redeploy.",
    });
  try {
    const target = new URL(backend);
    if (
      !(target.protocol === "https:" || (target.protocol === "http:" && ["127.0.0.1", "localhost"].includes(target.hostname))) || target.username || target.password
    )
      throw new Error("HTTPS required");
    target.pathname = path;
    target.search = incoming.search;
    const response = await fetch(target, {
      method: req.method,
      redirect: "error",
      signal: AbortSignal.timeout(110000),
      headers: {
        "Content-Type": "application/json",
        Cookie: (req.headers?.cookie || "").split(';').map(c=>c.trim()).filter(c=>c.startsWith('fynd_session=')).join('; '),
        "X-CSRF-Token": req.headers?.["x-csrf-token"] || "",
        "X-Requested-With": req.headers?.["x-requested-with"] || "",
      },
      ...(req.method === "POST"
        ? { body: JSON.stringify(req.body || {}) }
        : {}),
    });
    const body = await response.text();
    const cookies = response.headers.getSetCookie();
    if (cookies.length) res.setHeader("Set-Cookie", cookies);
    const retryAfter = response.headers.get("retry-after");
    if (retryAfter) res.setHeader("Retry-After", retryAfter);
    res.setHeader("Content-Type", "application/json");
    return res.status(response.status).send(body);
  } catch {
    return res.status(502).json({
      detail:
        "The backend could not be reached. Check its health and deployment configuration.",
    });
  }
}
