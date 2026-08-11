export function TierBadge({ tier }) {
  const t = (tier || "").toLowerCase();
  if (t === "gold") return <span className="badge gold" title="At least one independent operator confirms the answer-to-entity binding">gold witness</span>;
  if (t === "silver") return <span className="badge silver" title="Confirmed, but by a single independent operator">silver witness</span>;
  return <span className="badge bad">unwitnessed</span>;
}

/** Difficulty, measured — not asserted. Absent means never tested. */
export function StumpBadge({ stump }) {
  if (!stump) return <span className="badge silver" title="Never run against a solver">stump untested</span>;
  const r = stump.stump_rate;
  const d = stump.diagnosis || "";
  if (r === null || r === undefined) return <span className="badge silver">stump untested</span>;
  const pct = `${Math.round(r * 100)}%`;
  if (d.startsWith("DEAD")) return <span className="badge bad" title={d}>stump {pct} — dead</span>;
  if (d.startsWith("FRAGILE")) return <span className="badge warn" title={d}>stump {pct} — fragile</span>;
  if (d.startsWith("WATCH")) return <span className="badge info" title={d}>stump {pct} — watch</span>;
  return <span className="badge good" title={d}>stump {pct}</span>;
}

export function FieldBadge({ cls }) {
  if (cls === "identifier") return <span className="badge good" title="Opaque token: cannot be recalled from knowing the entity">identifier</span>;
  if (cls === "attribute") return <span className="badge warn" title="Memorable property: a solver may recall it once it identifies the entity">attribute</span>;
  return null;
}
