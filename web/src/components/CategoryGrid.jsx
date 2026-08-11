export default function CategoryGrid({ categories, active, onPick }) {
  if (!categories || !categories.length) return null;
  return (
    <div className="catgrid">
      <button
        className={"catcell" + (active === "" ? " active" : "")}
        onClick={() => onPick("")}
      >
        <span className="cname">all categories</span>
        <span className="cmeta">{categories.reduce((n, c) => n + (c.n_served || 0), 0)} traps</span>
      </button>
      {categories.map((c) => (
        <button
          key={c.category}
          className={
            "catcell" +
            (active === c.category ? " active" : "") +
            (c.n_served ? "" : " dead")
          }
          onClick={() => onPick(c.category)}
          title={c.refusal || ""}
        >
          <span className="cname">{c.category}</span>
          <span className="cmeta">
            {c.n_served
              ? `${c.n_served} trap${c.n_served > 1 ? "s" : ""}${c.tier ? ` · ${c.tier}` : ""}`
              : "refused — no independent witness"}
          </span>
        </button>
      ))}
    </div>
  );
}
