import { useState } from "react";
import { generate, jobStatus } from "../api.js";
import TrapCard from "./TrapCard.jsx";

/** Draw a prompt.
 *
 *  Two paths, and the difference is the point. The default DRAWS FROM STOCK:
 *  synchronous, and the prompt is spent on delivery, because a benchmark prompt
 *  a solver has already seen measures recall rather than the capability the
 *  trap probes. `mint` runs the live traversal instead, which takes minutes,
 *  may refuse, and books whatever it produces as spent too.
 *
 *  An empty pool is refused with HTTP 409 and the refusal is rendered with its
 *  counts. There is no path that re-serves a burned prompt.
 */
export default function GeneratePanel({ categories, pool, warmState, onSpend }) {
  const [cat, setCat] = useState("");
  const [busy, setBusy] = useState(false);
  const [mint, setMint] = useState(false);
  const [phase, setPhase] = useState("");
  const [err, setErr] = useState("");
  const [exhausted, setExhausted] = useState(null);
  const [note, setNote] = useState("");
  const [result, setResult] = useState(null);

  async function run() {
    if (!cat) return;
    setBusy(true); setErr(""); setResult(null); setExhausted(null); setNote("");
    setPhase(warmState === "asleep" ? "waking the origin (free tier cold start, ~50s)" : "submitting");
    try {
      const job = await generate(cat, { fresh: mint });
      // Pool draw: the answer is already in the response, no job to poll.
      if (job.result && !job.job_id) {
        setResult(job.result);
        setNote(
          `drawn from stock and spent · ${job.n_available} left in ${cat}` +
          (job.reissued ? " · reissued against an existing request key" : "")
        );
        setPhase("");
        if (onSpend) onSpend();
        return;
      }
      const id = job.job_id || job.id;
      if (!id) { setResult(job.trap || job); setPhase(""); return; }
      setPhase("traversing source APIs");
      for (let i = 0; i < 150; i++) {
        await new Promise((r) => setTimeout(r, 4000));
        const s = await jobStatus(id);
        if (s.status === "done" || s.status === "complete") {
          setResult(s.trap || s.result || s);
          if (s.pool) setNote(`minted and booked as spent · ${s.pool.n_available} left in ${cat}`);
          if (onSpend) onSpend();
          break;
        }
        if (s.status === "refused") throw new Error(s.detail || "the gate refused this seed");
        if (s.status === "error" || s.status === "failed") throw new Error(s.detail || s.error || "generation failed");
        setPhase((s.progress && s.progress.phase) || s.status || "traversing source APIs");
      }
    } catch (e) {
      if (e.status === 409 && e.body) setExhausted(e.body);
      else setErr(String(e.message || e));
    } finally {
      setBusy(false); setPhase("");
    }
  }

  const live = {};
  ((pool && pool.categories) || []).forEach((p) => { if (p.n_total) live[p.category] = p; });
  const havePool = Object.keys(live).length > 0;
  const options = havePool
    ? Object.values(live).map((p) => ({ category: p.category, n: p.n_available, total: p.n_total }))
    : (categories || []).filter((c) => c.n_served).map((c) => ({ category: c.category, n: null }));

  return (
    <section>
      <h2 className="sec">Draw a prompt</h2>
      <div className="notice">
        A prompt is single-use. Drawing one spends it, and the same prompt is never
        served twice — a repeated request inside a {pool ? pool.reissue_seconds : 600}s
        window returns the identical prompt so a network retry costs nothing, and after
        that it is burned. Prompts are not recycled: when a category runs out the API
        refuses with 409 and the pool is refilled by sweeping the seed roster, not by
        re-issuing spent prompts. Ticking <em>mint</em> re-runs the live traversal
        against the source APIs instead — minutes, not seconds, and it may refuse.
      </div>
      <div className="controls">
        <div className="ctl">
          <label htmlFor="gen-cat">category</label>
          <select id="gen-cat" value={cat} onChange={(e) => setCat(e.target.value)}>
            <option value="">choose…</option>
            {options.map((o) => (
              <option key={o.category} value={o.category} disabled={o.n === 0}>
                {o.category}{o.n === null ? "" : ` (${o.n}/${o.total} left)`}
              </option>
            ))}
          </select>
        </div>
        <label className="ctl" style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
          <input type="checkbox" checked={mint} onChange={(e) => setMint(e.target.checked)} />
          <span>mint a new one live</span>
        </label>
        <button onClick={run} disabled={busy || !cat}>
          {busy ? <><span className="spin" /> working</> : mint ? "mint" : "draw"}
        </button>
        {phase && <span style={{ color: "var(--mute)" }}>{phase}…</span>}
      </div>
      {exhausted && (
        <div className="notice err">
          <strong>{exhausted.category} is exhausted.</strong>{" "}
          {exhausted.n_burned} burned, {exhausted.n_served} still inside the reissue
          window, {exhausted.n_available} available of {exhausted.n_total}. {exhausted.detail}
        </div>
      )}
      {err && <div className="notice err">{err}</div>}
      {note && <div className="notice">{note}</div>}
      {result && result.prompt && <TrapCard trap={result} />}
      {result && !result.prompt && (
        <pre className="mono" style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(result, null, 1)}</pre>
      )}
    </section>
  );
}
