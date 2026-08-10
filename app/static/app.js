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

function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

/* ---------- generated trap detail ---------- */
function showGenerated(t) {
  const det = $("detail");
  let html = `<div class="prompt-text">${esc(t.prompt)}</div>`;
  html += `<div class="kv"><b>Answer:</b> ${esc(t.answer)} ${badge(t.api_proof, "api-proof")} ${badge(t.verified, t.verified ? "verified" : "unverified")}</div>`;
  html += `<div class="kv"><b>Paper:</b> ${esc(t.paper)} · <b>Date:</b> ${esc(t.date)} · <b>Field:</b> ${esc(t.field)}</div>`;
  html += `<div class="kv"><b>Confidence:</b> ${esc(t.confidence || "—")} · <b>Words:</b> ${t.word_count || "—"}</div>`;
  if (t.image_url) html += `<img src="${t.image_url}" alt="scan">`;
  if (t.golden) html += `<div class="kv"><b>Golden trajectory:</b></div><ol class="trace">` +
    t.golden.map((s) => `<li>${esc(s)}</li>`).join("") + `</ol>`;
  if (t.sources) html += `<div class="kv"><b>Sources:</b></div><ul class="trace">` +
    t.sources.map((s) => `<li><a href="${s}" target="_blank" rel="noopener">${esc(s)}</a></li>`).join("") + `</ul>`;
  if (!t.verified) html += `<div class="kv warn">OCR-derived candidate — confirm against the image before treating the answer as ground truth.</div>`;
  det.innerHTML = html;
}

/* ---------- catalogs ---------- */
async function loadGenerated() {
  const data = await api("/api/generated");
  $("gen-count").textContent = data.count;
  const list = $("generated-list");
  list.innerHTML = data.count ? "" : "<div class='hint'>None yet — generate one.</div>";
  data.verified.forEach((t) => {
    const div = document.createElement("div");
    div.className = "item";
    div.innerHTML = `<b>${esc(t.paper)}</b> ${esc(t.date)} ${badge(true, "verified")} ${badge(t.api_proof, "api-proof")}
      <div style="color:#6b6b6b;font-size:11px;margin-top:3px">${esc(t.field)}: ${esc(t.answer)}</div>`;
    div.onclick = () => { showGenerated(t); markActive(div); };
    list.appendChild(div);
  });
}

async function loadPending() {
  const data = await api("/api/pending");
  $("pend-count").textContent = data.count;
  const list = $("pending-list");
  // An empty queue is the healthy state, not a failure - it means every
  // OCR-derived candidate has already been confirmed into the verified pool.
  list.innerHTML = data.count ? ""
    : "<div class='hint'>Queue empty — every candidate has been confirmed into the verified pool. "
      + "New candidates land here after a walk.</div>";
  data.pending.forEach((t) => {
    const div = document.createElement("div");
    div.className = "item";
    div.innerHTML = `<b>${esc(t.paper)}</b> ${esc(t.date)} ${badge(false, "unverified")}
      <div style="color:#6b6b6b;font-size:11px;margin-top:3px">OCR read: ${esc(t.answer)}</div>`;
    div.onclick = () => { showGenerated(t); markActive(div); };
    list.appendChild(div);
  });
}

async function loadPrompts() {
  const data = await api("/api/prompts");
  prompts = data.prompts;
  const list = $("prompt-list");
  list.innerHTML = "";
  prompts.forEach((p) => {
    const div = document.createElement("div");
    div.className = "item";
    div.dataset.id = p.id;
    div.innerHTML = `<b>${p.id}</b> ${p.domain}
      ${badge(p.verified, p.verified ? "verified" : "fails")}
      ${badge(p.api_proof, "api-proof")}${p.withdrawn ? ' <span class="badge bad">WITHDRAWN</span>' : ""}
      <div style="color:#6b6b6b;font-size:11px;margin-top:3px">answer: ${p.answer}</div>`;
    div.onclick = () => selectPrompt(p.id, div);
    list.appendChild(div);
  });
}

function markActive(el) {
  document.querySelectorAll(".item").forEach((x) => x.classList.remove("active"));
  if (el) el.classList.add("active");
}

async function selectPrompt(pid, el) {
  markActive(el);
  selected = pid;
  const d = await api(`/api/prompts/${pid}`);
  const det = $("detail");
  let html = `<div class="prompt-text">${esc(d.prompt)}</div>`;
  html += `<div class="kv"><b>Answer:</b> ${esc(d.answer)} ${badge(d.api_proof, "api-proof")}</div>`;
  if (d.withdrawn) {
    html += `<div class="kv bad"><b>WITHDRAWN &mdash; not a valid trap.</b> ${esc(d.withdrawn_reason || "")}` +
            (d.withdrawn_evidence ? `<br><span class="muted">evidence: ${esc(d.withdrawn_evidence)}</span>` : "") + `</div>`;
  }
  html += `<div class="kv"><b>Method:</b> ${esc(d.method)} · <b>Domain:</b> ${esc(d.domain)}</div>`;
  if (d.has_image) html += `<img src="${d.image_url}" alt="scan">`;
  html += `<div class="kv"><b>Golden trajectory:</b></div><ol class="trace">` +
    d.golden.map((s) => `<li>${esc(s)}</li>`).join("") + `</ol>`;
  html += `<div class="kv"><b>Sources:</b></div><ul class="trace">` +
    d.sources.map((s) => `<li><a href="${s}" target="_blank" rel="noopener">${esc(s)}</a></li>`).join("") + `</ul>`;
  det.innerHTML = html;
}

/* ---------- on-demand generation (async job + polling) ---------- */
$("gen-btn").onclick = async () => {
  const out = $("gen-result");
  const body = { trap_class: "vision" };
  const lccn = $("gen-lccn").value;
  const date = $("gen-date").value.trim();
  if (lccn) body.lccn = lccn;
  if (date) body.start_date = date;
  try {
    const job = await api("/api/generate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    out.textContent = "Starting walk…";
    // Poll until the job completes, surfacing which page is being worked and why
    // pages get rejected. A silent spinner is indistinguishable from a hang.
    // Budget must cover the real worst case, not a guess. A walk costs roughly
    // 2 min/page on the free tier (three raster downloads + OCR each), so an
    // 8-page walk can legitimately run ~17 min. The old 180x2s = 6 min budget
    // declared "timeout" on jobs that were working fine.
    let done = null, err = null, lastLog = [];
    const deadline = Date.now() + 22 * 60 * 1000;
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 3000));
      const s = await api(`/api/generate/${job.job_id}`);
      if (s.log && s.log.length) lastLog = s.log;
      if (s.status === "done") { done = s.result; break; }
      if (s.status === "error") { err = s.detail; break; }
      const p = s.progress || {};
      const step = p.step ? `page ${p.step}/${s.max_steps || "?"}` : "starting";
      const where = p.date ? ` · ${esc(p.date)}` : "";
      const votes = (p.votes && p.votes.length)
        ? `<br><span class="muted">votes so far: ${esc(p.votes.join(", "))} (needs 2 agreeing)</span>` : "";
      const why = p.reason ? `<br><span class="muted">rejected: ${esc(p.reason)}</span>`
                : p.candidate ? `<br><span class="muted">candidate ${esc(p.candidate)} — testing api-proof</span>`
                : votes;
      out.innerHTML = `<b>${esc(step)}</b>${where} — ${esc(p.phase || "working")}` +
        ` <span class="muted">(${Math.round(s.elapsed || 0)}s)</span>${why}`;
    }
    const trail = lastLog.filter((e) => e.reason)
      .map((e) => `<div class="muted">${esc(e.date || "")} — ${esc(e.reason)}</div>`).join("");
    if (err) {
      out.innerHTML = `${badge(false, "no trap")} ${esc(err)}` +
        (trail ? `<div style="margin-top:6px">${trail}</div>` : "");
      return;
    }
    if (!done) {
      out.innerHTML = `${badge(false, "still running")} the walk exceeded this page's 22-minute ` +
        `watch window but is <b>not</b> cancelled — it continues server-side. ` +
        `Poll <code>/api/generate/${esc(job.job_id)}</code> or check the pending queue.` +
        (trail ? `<div style="margin-top:6px">${trail}</div>` : "");
      return;
    }
    const r = done;
    out.innerHTML = `${badge(r.api_proof, "api-proof")} <b>${esc(r.paper)}</b> ${esc(r.date)}<br>` +
      `${esc(r.field)}: <b>${esc(r.answer)}</b> ${badge(r.verified, r.verified ? "verified" : "unverified")}`;
    showGenerated(r);
    loadPending();  // the new candidate joins the pending queue
  } catch (e) { out.innerHTML = `${badge(false, "error")} ${esc(e.message)}`; }
};

/* ---------- stress test ---------- */
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
    let html = `<div style="color:#6b6b6b">${esc(r.note)}</div><table><tr><th>ID</th><th>L2 fail</th><th>stump?</th></tr>`;
    for (const [pid, res] of Object.entries(r.results)) {
      html += `<tr><td>${pid}</td><td>${(res.l2_fail_rate * 100).toFixed(0)}%</td>` +
        `<td>${badge(res.proxy_validated, res.proxy_validated ? "proxy" : "no")}</td></tr>`;
    }
    out.innerHTML = html + "</table>";
  } catch (e) { out.innerHTML = `${badge(false, "error")} ${esc(e.message)}`; }
};

loadGenerated().catch((e) => { $("generated-list").textContent = "Error: " + e.message; });
loadPending().catch((e) => { $("pending-list").textContent = "Error: " + e.message; });
loadPrompts().catch((e) => { $("prompt-list").textContent = "Error: " + e.message; });
