import { useState } from "react";
import { TierBadge, StumpBadge, FieldBadge } from "./Badges.jsx";

function num(v) {
  if (v === 0) return "0";
  if (v === null || v === undefined || v === "") return "—";
  return typeof v === "number" ? (Number.isInteger(v) ? String(v) : v.toFixed(4)) : String(v);
}

export default function TrapCard({ trap }) {
  const [shown, setShown] = useState(false);
  const defects = Object.entries(trap.known_defects || {});
  const ops = trap.source_operators || [];
  const conf = trap.independent_confirming_operators || trap.confirming_operators || [];
  const st = trap.stump;

  return (
    <article className="card">
      <div className="hd">
        <span className="cat">{trap.category}</span>
        <TierBadge tier={trap.witness_tier} />
        <StumpBadge stump={st} />
        <FieldBadge cls={trap.field_class} />
        <span className="spacer" />
        <span className="mono">{trap.field}</span>
      </div>
      <div className="bd">
        <p className="prompt">{trap.prompt || <em>no prompt recorded</em>}</p>

        <dl className="kv">
          <dt>Answer</dt>
          <dd>
            <span
              className={"answer" + (shown ? "" : " blur")}
              onClick={() => setShown(!shown)}
              title="click to reveal"
            >
              {String(trap.answer)}
            </span>
          </dd>
          <dt>Entity</dt><dd>{trap.entity || "—"}</dd>
          <dt>Ranked population</dt><dd>{num(trap.n_base)}</dd>
          <dt>Primary operator</dt><dd>{trap.primary_operator || "—"}</dd>
          <dt>Independent witnesses</dt>
          <dd>{conf.length ? conf.join(", ") : <em>none</em>}</dd>
          {trap.witness_scope && (<><dt>Witness scope</dt><dd>{trap.witness_scope}</dd></>)}
          {st && (<>
            <dt>Closed-book solve rate</dt>
            <dd>{num(st.A_accuracy)} <span style={{ color: "var(--mute)" }}>(trap prompt alone)</span></dd>
            <dt>Answer recalled from entity</dt>
            <dd>{num(st.B_accuracy)} <span style={{ color: "var(--mute)" }}>(entity handed to the solver)</span></dd>
            <dt>Entity identified from prompt</dt>
            <dd>{num(st.C_accuracy)}</dd>
          </>)}
        </dl>

        {defects.length > 0 && (
          <div className="defects">
            <b>Measured defects — disclosed, not hidden</b>
            <ul>{defects.map(([k, v]) => <li key={k}><code>{k.replace(/^known_defect_/, "")}</code>: {String(v)}</li>)}</ul>
          </div>
        )}

        {st && st.diagnosis && !st.diagnosis.startsWith("HEALTHY") && (
          <div className="defects">
            <b>Difficulty diagnosis</b>
            <div>{st.diagnosis}</div>
          </div>
        )}

        <div className="srcs">
          <div>Operators: {ops.length ? ops.map((o) => <code key={o}>{o}</code>).reduce((a, b) => [a, " ", b]) : "—"}</div>
          {trap.confirmation && <div style={{ marginTop: 4 }}>{trap.confirmation}</div>}
        </div>
      </div>
    </article>
  );
}
