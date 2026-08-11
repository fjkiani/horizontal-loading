import { useState } from "react";
import { generate, jobStatus } from "../api.js";
import TrapCard from "./TrapCard.jsx";

/** Live traversal against the source APIs. Minutes, not seconds — and the origin
 *  may be cold, so the panel reports which of the two waits you are in. */
export default function GeneratePanel({ categories, warmState }) {
  const [cat, setCat] = useState("");
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState("");
  const [err, setErr] = useState("");
  const [result, setResult] = useState(null);

  async function run() {
    if (!cat) return;
    setBusy(true); setErr(""); setResult(null);
    setPhase(warmState === "asleep" ? "waking the origin (free tier cold start, ~50s)" : "submitting");
    try {
      const job = await generate(cat, null);
      const id = job.job_id || job.id;
      if (!id) { setResult(job.trap || job); setPhase(""); return; }
      setPhase("traversing source APIs");
      for (let i = 0; i < 150; i++) {
        await new Promise((r) => setTimeout(r, 4000));
        const s = await jobStatus(id);
        if (s.status === "done" || s.status === "complete") { setResult(s.trap || s.result || s); break; }
        if (s.status === "error" || s.status === "failed") throw new Error(s.error || "generation failed");
        setPhase(s.stage || s.status || "traversing source APIs");
      }
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false); setPhase("");
    }
  }

  const servable = (categories || []).filter((c) => c.n_served);

  return (
    <section>
      <h2 className="sec">Generate on demand</h2>
      <div className="notice">
        Generation re-runs the live traversal against the source APIs and re-derives
        the answer, the ranking statistics and the witness. It is not a lookup, so it
        takes minutes. A refused category stays refused: the gate is applied on write.
      </div>
      <div className="controls">
        <div className="ctl">
          <label htmlFor="gen-cat">category</label>
          <select id="gen-cat" value={cat} onChange={(e) => setCat(e.target.value)}>
            <option value="">choose…</option>
            {servable.map((c) => (
              <option key={c.category} value={c.category}>{c.category}</option>
            ))}
          </select>
        </div>
        <button onClick={run} disabled={busy || !cat}>
          {busy ? <><span className="spin" /> working</> : "generate"}
        </button>
        {phase && <span style={{ color: "var(--mute)" }}>{phase}…</span>}
      </div>
      {err && <div className="notice err">{err}</div>}
      {result && result.prompt && <TrapCard trap={result} />}
      {result && !result.prompt && (
        <pre className="mono" style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(result, null, 1)}</pre>
      )}
    </section>
  );
}
