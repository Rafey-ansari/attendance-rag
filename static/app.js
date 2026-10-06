const $ = s => document.querySelector(s);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };
async function api(url, opt = {}) {
  const r = await fetch(url, { credentials: "same-origin", ...opt });
  let d = {}; try { d = await r.json(); } catch {}
  if (!r.ok) throw new Error(d.error || r.statusText);
  return d;
}
const post = (url, body) => api(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const show = authed => { $("#auth").classList.toggle("hidden", authed); $("#app").classList.toggle("hidden", !authed); };

let mode = "login";
function setMode(m) {
  mode = m;
  $("#tab-login").classList.toggle("on", m === "login"); $("#tab-signup").classList.toggle("on", m === "signup");
  $("#authBtn").textContent = m === "login" ? "Login" : "Create account";
}
$("#tab-login").onclick = () => setMode("login");
$("#tab-signup").onclick = () => setMode("signup");
$("#authForm").onsubmit = async e => {
  e.preventDefault(); $("#authErr").textContent = "";
  try { await post("/api/" + mode, { email: $("#email").value, password: $("#password").value }); boot(); }
  catch (x) { $("#authErr").textContent = x.message; }
};
$("#logout").onclick = async () => { await post("/api/logout", {}); $("#log").replaceChildren(); show(false); };

async function boot() {
  try { const me = await api("/api/me"); $("#who").textContent = me.email; show(true); refreshFiles(); loadHistory(); }
  catch { show(false); }
}
async function refreshFiles() {
  const list = await api("/api/files");
  $("#files").replaceChildren(...list.map(f => {
    const li = el("li", "", `${f.name} (${f.n_records} rows)`);
    const b = el("button", "", "✕");
    b.onclick = async () => { await api("/api/files/" + f.id, { method: "DELETE" }); refreshFiles(); };
    li.append(b); return li;
  }));
}
$("#fileInput").onchange = async e => {
  const fd = new FormData(); [...e.target.files].forEach(f => fd.append("files", f));
  $("#upMsg").textContent = "Processing (OCR/extraction can take a minute)…";
  try {
    const res = await api("/api/upload", { method: "POST", body: fd });
    $("#upMsg").textContent = res.map(r => r.error ? `${r.file}: ${r.error}` : `${r.file}: ${r.records} records`).join(" | ");
  } catch (x) { $("#upMsg").textContent = x.message; }
  e.target.value = ""; refreshFiles();
};

function addMsg(role, text, m) {
  const b = el("div", "msg " + role, text);
  if (m && m.sources && m.sources.length) {
    const d = el("details", "src"); d.append(el("summary", "", `Sources (${m.sources.length})`));
    m.sources.forEach(s => d.append(el("div", "s", `${s.file} — ${s.ref}${s.text ? ": " + s.text : ""}`)));
    b.append(d);
  }
if (role === "bot" && m && m.id) {
  const f = el("div", "fb");

  const buttons = el("div", "fb-buttons");

  let selectedRating = null;

  [["👍", 1], ["👎", -1]].forEach(([icon, rating]) => {
    const btn = el("button", "", icon);

    btn.onclick = () => {
      selectedRating = rating;

      // Highlight selected button
      [...buttons.children].forEach(x => x.classList.remove("selected"));
      btn.classList.add("selected");

      feedbackBox.style.display = "block";
      feedbackInput.focus();
    };

    buttons.append(btn);
  });

  const feedbackBox = el("div", "feedback-box");
  feedbackBox.style.display = "none";

  const feedbackInput = document.createElement("textarea");
  feedbackInput.placeholder = "Tell us what was good or what should be improved...";
  feedbackInput.rows = 3;
  feedbackInput.maxLength = 500;

  const submitBtn = el("button", "primary", "Submit feedback");

  submitBtn.onclick = async () => {
    if (!selectedRating) {
      alert("Please select 👍 or 👎 first.");
      return;
    }

    const comment = feedbackInput.value.trim();

    try {
      await post("/api/feedback", {
        message_id: m.id,
        rating: selectedRating,
        comment
      });

      feedbackBox.innerHTML = "";
      feedbackBox.append(
        el("span", "muted", "Thanks — your feedback will be used in future answers.")
      );
    } catch (x) {
      alert(x.message);
    }
  };

  feedbackBox.append(feedbackInput, submitBtn);
  f.append(buttons, feedbackBox);
  b.append(f);
}

  $("#log").append(b); $("#log").scrollTop = 1e9;
}
async function loadHistory() {
  $("#log").replaceChildren();
  (await api("/api/history")).forEach(h => { addMsg("user", h.question); addMsg("bot", h.answer, h); });
}
async function send() {
  const question = $("#q").value.trim(); if (!question) return;
  $("#q").value = ""; addMsg("user", question);
  try { const r = await post("/api/chat", { question }); addMsg("bot", r.answer, r); }
  catch (x) { addMsg("bot", "Error: " + x.message); }
}
$("#send").onclick = send;
$("#q").onkeydown = e => { if (e.key === "Enter") send(); };
boot();
