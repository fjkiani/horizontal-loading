// The catalog is baked into the build, so the console renders instantly with the
// origin asleep. The API is only needed for live generation; we start Render's
// cold boot on first paint so it is usually awake by the time anyone clicks.

const TIMEOUT_MS = 90000;

export async function req(path, opts = {}) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), opts.timeout || TIMEOUT_MS);
  try {
    const r = await fetch(path, { ...opts, signal: ctl.signal });
    const text = await r.text();
    let body = null;
    try { body = text ? JSON.parse(text) : null; } catch { body = { raw: text }; }
    if (!r.ok) {
      const msg = (body && (body.detail || body.error)) || `HTTP ${r.status}`;
      throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    }
    return body;
  } finally {
    clearTimeout(t);
  }
}

/** Load the static catalog first; fall back to the live API only if absent. */
export async function loadCatalog() {
  try {
    const c = await req("/catalog.json", { timeout: 15000 });
    if (c && Array.isArray(c.traps)) return { ...c, source: "static" };
  } catch { /* fall through to the API */ }
  const live = await req("/api/generated");
  return { traps: live.verified || [], categories: [], source: "api" };
}

/** Fire-and-forget cold-boot kick. Never throws. */
export function warm() {
  return req("/api/health", { timeout: 120000 }).then(
    () => "awake",
    () => "asleep"
  );
}

export function generate(category, seed) {
  return req("/api/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ category, seed: seed || null }),
  });
}

export function jobStatus(id) {
  return req(`/api/generate/${id}`, { timeout: 30000 });
}
