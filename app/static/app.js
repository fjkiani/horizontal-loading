const $ = (id) => document.getElementById(id);
let prompts = [];
let selected = null;

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    const t = await r.json().catch(() => ({ detail: r.statusText }));
    throw new Error(t.detail || r.statusText);
  }
  return r.json();
}

function badge(ok, label) {
  const cls = ok === true ? "ok" : ok === false ? "bad" : "neutral";
  return `<span class="badge ${cls}">${label}</span>`;
}

async function loadPrompts() {
  const data = await api("/api/prompts");
  prompts = data.prompts;
  const list = $("prompt-list");
  list.innerHTML = "";
  const sel = $("gen-page");
  sel.innerHTML = "";
  prompts.forEach((p) => {
    const div = document.createElement("div");
    div.className = "item";
    div.dataset.id = p.id;
    div.innerHTML = `<b>${p.id}</b> ${p.domain}
      ${badge(p.verified, p.verified ? "verified" : "fails")}
      ${badge(p.api_proof, "api-proof")}
      <div style="color:#6b6b6b;font-size:11px;margin-top:3px">answer: ${p.answer}</div>`;
    div.onclick = () => selectPrompt(p.id, div);
    list.appendChild(div);
    const o = document.createElement("option");
    o.value = p.id; o.textContent = `${p.id} (${p.domain})`;
    sel.appendChild(o);
  });
}

async function selectPrompt(pid, el) {
  document.querySelectorAll(".item").forEach((x) => x.classList.remove("active"));
  if (el) el.classList.add("active");
  selected = pid;
  const d = await api(`/api/prompts/${pid}`);
  const det = $("detail");
  let html = `<div class="prompt-text">${d.prompt}</div>`;
  html += `<div class="kv"><b>Answer:</b> ${d.answer} ${badge(d.api_proof, "api-proof")}</div>`;
  html += `<div class="kv"><b>Method:</b> ${d.method} · <b>Domain:</b> ${d.domain}</div>`;
  if (d.has_image) html += `<img src="${d.image_url}" alt="scan">`;
  html += `<div class="kv"><b>Golden trajectory:</b></div><ol class="trace">` +
          d.golden.map((s) => `<li>${s}</li>`).join("") + `</ol>`;
  html += `<div class="kv"><b>Sources:</b></div><ul class="trace">` +
          d.sources.map((s) => `<li><a href="${s}" target="_blank" rel="noopener">${s}</a></li>`).join("") + `</ul>`;
  det.innerHTML = html;
}

$("gen-btn").onclick = async () => {
  const out = $("gen-result");
  out.textContent = "Verifying live…";
  try {
    const r = await api("/api/generate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ trap_class: "vision", page_id: $("gen-page").value }),
    });
    out.innerHTML = `${badge(true, "verified")} <b>${r.id}</b> answer: <b>${r.answer}</b> (api-proof)`;
  } catch (e) { out.innerHTML = `${badge(false, "error")} ${e.message}`; }
};

$("solver").onchange = () => {
  $("openai-fields").classList.toggle("hidden", $("solver").value !== "openai");
};

$("stress-btn").onclick = async () => {
  const out = $("stress-result");
  out.textContent = "Running…";
  const body = {
    prompt_ids: selected ? [selected] : prompts.map((p) => p.id),
    solver: $("solver").value,
    n_runs: parseInt($("n-runs").value || "3", 10),
  };
  if (body.solver === "openai") {
    body.api_key = $("api-key").value; body.base_url = $("base-url").value;
    body.model = $("model").value || "gpt-4o";
  }
  try {
    const r = await api("/api/stress_test", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    let html = `<div style="color:#6b6b6b">${r.note}</div><table><tr><th>ID</th><th>L2 fail</th><th>stump?</th></tr>`;
    for (const [pid, res] of Object.entries(r.results)) {
      html += `<tr><td>${pid}</td><td>${(res.l2_fail_rate * 100).toFixed(0)}%</td>` +
              `<td>${badge(res.proxy_validated, res.proxy_validated ? "proxy" : "no")}</td></tr>`;
    }
    out.innerHTML = html + "</table>";
  } catch (e) { out.innerHTML = `${badge(false, "error")} ${e.message}`; }
};

loadPrompts().catch((e) => { $("prompt-list").textContent = "Error: " + e.message; });
