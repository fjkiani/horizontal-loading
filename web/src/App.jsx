import { useEffect, useMemo, useState } from "react";
import { loadCatalog, warm, poolStatus } from "./api.js";
import CategoryGrid from "./components/CategoryGrid.jsx";
import TrapCard from "./components/TrapCard.jsx";
import GeneratePanel from "./components/GeneratePanel.jsx";

export default function App() {
  const [cat, setCat] = useState("");
  const [tier, setTier] = useState("");
  const [stumpOnly, setStumpOnly] = useState(false);
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [warmState, setWarmState] = useState("unknown");
  const [pool, setPool] = useState(null);

  // Consumption is live state and cannot be baked into the build, so the pool
  // readout only appears once the origin answers. Its absence is not an error:
  // the catalog still renders with the origin asleep, it just shows how many
  // traps EXIST rather than how many are unspent.
  const refreshPool = () => poolStatus().then(setPool, () => setPool(null));

  useEffect(() => {
    loadCatalog().then(setData, (e) => setErr(String(e.message || e)));
    warm().then((w) => { setWarmState(w); if (w === "awake") refreshPool(); });
  }, []);

  const traps = data ? data.traps : [];

  const shown = useMemo(
    () =>
      traps.filter(
        (t) =>
          (!cat || t.category === cat) &&
          (!tier || t.witness_tier === tier) &&
          (!stumpOnly || (t.stump && (t.stump.stump_rate || 0) >= 0.67))
      ),
    [traps, cat, tier, stumpOnly]
  );

  const stats = useMemo(() => {
    const tested = traps.filter(
      (t) => t.stump && t.stump.stump_rate !== null && t.stump.stump_rate !== undefined
    );
    const dead = tested.filter((t) => (t.stump.diagnosis || "").startsWith("DEAD"));
    const mean = tested.length
      ? tested.reduce((a, t) => a + t.stump.stump_rate, 0) / tested.length
      : null;
    return {
      served: traps.length,
      gold: traps.filter((t) => t.witness_tier === "gold").length,
      tested: tested.length,
      dead: dead.length,
      mean,
      refused: data && data.categories ? data.categories.filter((c) => !c.n_served).length : 0,
    };
  }, [traps, data]);

  return (
    <div className="wrap">
      <header className="top">
        <h1>Seal — adversarial trap console</h1>
        <p className="sub">
          Prompts whose answers are derived by ranking a live API population, then
          confirmed by an operator that does not run the primary source. Every trap
          carries its leakage statistics, its witness tier and — where it has been
          run — its measured solve rate against a closed-book solver.
        </p>
        <div className="statrow">
          <div className="stat"><span className="k">served</span><span className="v">{stats.served}</span></div>
          <div className="stat"><span className="k">gold witness</span><span className="v">{stats.gold}</span></div>
          <div className="stat"><span className="k">stump-tested</span><span className="v">{stats.tested}</span></div>
          <div className="stat"><span className="k">dead traps</span><span className="v">{stats.dead}</span></div>
          <div className="stat"><span className="k">mean stump</span><span className="v">{stats.mean === null ? "—" : Math.round(stats.mean * 100) + "%"}</span></div>
          <div className="stat"><span className="k">refused cats</span><span className="v">{stats.refused}</span></div>
          <div className="stat">
            <span className="k">unspent</span>
            <span className="v">{pool ? pool.n_available_total : "—"}</span>
          </div>
          <div className="stat">
            <span className="k">burned</span>
            <span className="v">{pool ? pool.n_burned_total : "—"}</span>
          </div>
        </div>
        {pool && pool.low_water_categories.length > 0 && (
          <div className="notice">
            low water ({pool.low_water_mark} or fewer left):{" "}
            {pool.low_water_categories.join(", ")}. Prompts are not recycled — refill
            by sweeping the seed roster and re-baking.
          </div>
        )}
      </header>

      {err && <div className="notice err">catalog unavailable: {err}</div>}
      {!data && !err && <div className="notice"><span className="spin" /> loading catalog…</div>}

      {data && (
        <>
          <CategoryGrid categories={data.categories} pool={pool} active={cat} onPick={setCat} />

          <div className="controls">
            <div className="ctl">
              <label htmlFor="tier">witness tier</label>
              <select id="tier" value={tier} onChange={(e) => setTier(e.target.value)}>
                <option value="">any</option>
                <option value="gold">gold only</option>
                <option value="silver">silver only</option>
              </select>
            </div>
            <div className="ctl">
              <label htmlFor="so">solver difficulty</label>
              <select id="so" value="" disabled title="No solver measurement exists: the available key is a trial key capped at 1000 calls/month and is exhausted. Every gate here measures leakage, not difficulty.">
                <option value="">not measured</option>
              </select>
            </div>
            <button className="ghost" onClick={() => { setCat(""); setTier(""); setStumpOnly(false); }}>reset</button>
            <span style={{ color: "var(--mute)" }}>
              {shown.length} of {traps.length} shown · catalog {data.source}
              {data.generated_at ? " · built " + data.generated_at : ""}
            </span>
          </div>

          {shown.length === 0 && <div className="empty">No trap matches these filters.</div>}
          {shown.map((t) => <TrapCard key={t.category + "-" + t.answer} trap={t} />)}

          <GeneratePanel categories={data.categories} pool={pool}
                         warmState={warmState} onSpend={refreshPool} />
        </>
      )}
    </div>
  );
}
