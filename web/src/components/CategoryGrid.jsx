/** Category picker.
 *
 *  The cell shows two different numbers and they must not be confused. The
 *  baked catalog says how many traps EXIST for the category; the live pool says
 *  how many are still UNSPENT. Before the ledger only the first existed, so a
 *  category read as stocked forever no matter how many prompts had been handed
 *  out. When live pool data is present it wins, and a category that has been
 *  drained is labelled `exhausted` -- which is a different state from `refused`
 *  (no trap was ever built) and carries a different remedy.
 */
function cellMeta(c, live) {
  if (!c.n_served && !(live && live.n_total)) {
    return { text: "refused — no independent witness", cls: " dead" };
  }
  if (live) {
    if (live.n_available === 0) {
      return {
        text: `exhausted — ${live.n_burned + live.n_in_window} of ${live.n_total} spent`,
        cls: " spent",
      };
    }
    return {
      text:
        `${live.n_available} of ${live.n_total} left` +
        (c.tier ? ` · ${c.tier}` : "") +
        (live.low_water ? " · low" : ""),
      cls: live.low_water ? " low" : "",
    };
  }
  return {
    text: `${c.n_served} trap${c.n_served > 1 ? "s" : ""}${c.tier ? ` · ${c.tier}` : ""}`,
    cls: "",
  };
}

export default function CategoryGrid({ categories, pool, active, onPick }) {
  if (!categories || !categories.length) return null;
  const live = {};
  ((pool && pool.categories) || []).forEach((p) => {
    if (p.n_total) live[p.category] = p;
  });
  const havePool = Object.keys(live).length > 0;
  const total = havePool
    ? Object.values(live).reduce((n, p) => n + p.n_available, 0)
    : categories.reduce((n, c) => n + (c.n_served || 0), 0);

  return (
    <div className="catgrid">
      <button
        className={"catcell" + (active === "" ? " active" : "")}
        onClick={() => onPick("")}
      >
        <span className="cname">all categories</span>
        <span className="cmeta">
          {total} {havePool ? "unspent" : "traps"}
        </span>
      </button>
      {categories.map((c) => {
        const meta = cellMeta(c, live[c.category]);
        return (
          <button
            key={c.category}
            className={
              "catcell" + (active === c.category ? " active" : "") + meta.cls
            }
            onClick={() => onPick(c.category)}
            title={c.refusal || ""}
          >
            <span className="cname">{c.category}</span>
            <span className="cmeta">{meta.text}</span>
          </button>
        );
      })}
    </div>
  );
}
