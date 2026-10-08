let csrfToken = "";
export function setSession(session) {
  csrfToken = session?.csrf_token || "";
}
export async function authenticatedFetch(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    credentials: "same-origin",
    headers: {
      ...options.headers,
      "X-Requested-With": "fynd-console",
      ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
    },
  });
  if (response.status === 401 && !url.endsWith("/auth/login")) {
    setSession(null);
    window.dispatchEvent(new Event("fynd-session-expired"));
  }
  return response;
}
