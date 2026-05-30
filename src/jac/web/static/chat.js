// JAC web chat (Slice 2). Vanilla JS: EventSource for the agent event stream,
// fetch for sending messages and resolving HITL approvals/clarifications.
(() => {
  "use strict";

  const transcript = document.getElementById("transcript");
  const form = document.getElementById("chat-form");
  const textarea = document.getElementById("chat-text");
  const sendBtn = document.getElementById("chat-send");
  const newBtn = document.getElementById("chat-new");
  const sessionLabel = document.getElementById("chat-session");

  let assistantEl = null; // current streaming assistant bubble
  let lastToolEl = null; // last tool chip, for completion status

  // ----- DOM helpers -----
  const atBottom = () =>
    transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 80;

  function add(cls, html) {
    const wasBottom = atBottom();
    const el = document.createElement("div");
    el.className = "msg " + cls;
    if (html !== undefined) el.innerHTML = html;
    transcript.appendChild(el);
    if (wasBottom) transcript.scrollTop = transcript.scrollHeight;
    return el;
  }

  const esc = (s) =>
    String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );

  function setBusy(busy) {
    sendBtn.disabled = busy;
    textarea.disabled = busy;
    sendBtn.textContent = busy ? "…" : "Send";
  }

  // ----- frame dispatch -----
  function handle(f) {
    switch (f.type) {
      case "SessionStarted":
        sessionLabel.textContent = f.id || "—";
        break;
      case "UserMessage":
        assistantEl = null;
        lastToolEl = null;
        add("user", esc(f.content));
        setBusy(true);
        break;
      case "ModelRequestStarted":
        if (!assistantEl) {
          assistantEl = add("assistant thinking", "<span class='dots'>…</span>");
        }
        break;
      case "TextDelta":
        if (!assistantEl || assistantEl.classList.contains("thinking")) {
          assistantEl = add("assistant", "");
        }
        assistantEl.textContent += f.content;
        if (atBottom()) transcript.scrollTop = transcript.scrollHeight;
        break;
      case "ToolCallStarted": {
        const reason = f.reason ? " — " + esc(f.reason) : "";
        lastToolEl = add("tool", `🔧 <b>${esc(f.tool_name)}</b>${reason} <span class="tstat">running…</span>`);
        break;
      }
      case "ToolCallCompleted":
        markTool("ok", "done");
        break;
      case "ToolCallFailed":
        markTool("err", "failed: " + esc(f.error));
        break;
      case "ApprovalRequest":
        renderApproval(f);
        break;
      case "ModeAutoDecision":
        add("notice", `mode <b>${esc(f.mode)}</b> auto-${esc(f.decision)}ed <b>${esc(f.tool_name)}</b>`);
        break;
      case "ClarifyRequest":
        renderClarify(f);
        break;
      case "PlanReplaced":
        renderPlan(f.steps || []);
        break;
      case "PlanStepUpdated":
        add("notice", `plan step ${f.index} → ${esc(f.status)}: ${esc(f.text)}`);
        break;
      case "SubAgentSpawned":
        add("minion", `🐙 <b>${esc(f.spawn_id)}</b> (${esc(f.tier)}) — ${esc(f.objective)}`);
        break;
      case "SubAgentQuestion":
        add("minion", `🐙 <b>${esc(f.spawn_id)}</b> asks: ${esc(f.question)}`);
        break;
      case "SubAgentAnswer":
        add("minion", `↳ to <b>${esc(f.spawn_id)}</b>: ${esc(f.answer)}`);
        break;
      case "SubAgentCompleted":
        add("minion", `🐙 <b>${esc(f.spawn_id)}</b> done (${esc(f.exit_status)}, ${f.turns_used} turns)`);
        break;
      case "BudgetWarning":
        add("notice", `budget ${esc(f.kind)} at ${f.pct}% (${f.used}/${f.budget})`);
        break;
      case "BudgetHardStop":
        add("error", `token budget hit (${esc(f.kind)}). ${esc(f.suggested_action)}`);
        break;
      case "CompactionWarning":
        add("notice", `context at ${f.usage_pct}% — compaction near`);
        break;
      case "CompactionTriggered":
        add("notice", `compacted ${f.dropped_count} message(s) → ~${f.summary_tokens} tokens`);
        break;
      case "CompactionRefused":
        add("error", `context too full (${f.usage_pct}%). ${esc(f.suggested_action)}`);
        break;
      case "RunCompleted":
        if (assistantEl && !assistantEl.classList.contains("tool")) {
          assistantEl.classList.remove("thinking");
          if (f.output) assistantEl.textContent = f.output;
        } else if (f.output) {
          add("assistant", esc(f.output));
        }
        assistantEl = null;
        break;
      case "RunFailed":
        add("error", esc(f.error));
        assistantEl = null;
        break;
      case "Error":
        add("error", esc(f.error));
        setBusy(false);
        break;
      case "TurnDone":
        setBusy(false);
        textarea.focus();
        pollDashboard();
        break;
      case "Notice":
        add("notice", esc(f.text));
        break;
      default:
        break; // unknown frame types are ignored, not fatal
    }
  }

  function markTool(cls, label) {
    if (!lastToolEl) return;
    const stat = lastToolEl.querySelector(".tstat");
    if (stat) {
      stat.textContent = label;
      stat.className = "tstat " + cls;
    }
  }

  function renderPlan(steps) {
    const rows = steps
      .map((s) => {
        const mark = s.status === "completed" ? "✓" : s.status === "in_progress" ? "▸" : "○";
        return `<div class="plan-step ${esc(s.status)}">${mark} ${esc(s.text)}</div>`;
      })
      .join("");
    add("plan", `<div class="plan-title">plan</div>${rows}`);
  }

  function renderApproval(f) {
    const card = add("approval");
    const who = f.agent_label && f.agent_label !== "Gru" ? ` <span class="who">${esc(f.agent_label)}</span>` : "";
    const reason = f.reason ? `<div class="ar-reason">${esc(f.reason)}</div>` : "";
    let args = "";
    try {
      args = `<pre class="ar-args">${esc(JSON.stringify(f.args, null, 2))}</pre>`;
    } catch (e) {}
    card.innerHTML =
      `<div class="ar-head">approve <b>${esc(f.tool_name)}</b>?${who}</div>${reason}${args}` +
      `<input class="ar-fb" type="text" placeholder="optional: redirect instead of approving">` +
      `<div class="ar-btns"><button class="ar-yes">Approve</button>` +
      `<button class="ar-no danger">Deny</button></div>`;
    const done = (approved) => {
      const feedback = card.querySelector(".ar-fb").value || null;
      post("/chat/approve", { id: f.tool_call_id, approved, feedback });
      card.querySelector(".ar-btns").innerHTML =
        `<span class="ar-result">${approved ? "approved" : feedback ? "redirected" : "denied"}</span>`;
      card.querySelector(".ar-fb").remove();
    };
    card.querySelector(".ar-yes").onclick = () => done(true);
    card.querySelector(".ar-no").onclick = () => done(false);
  }

  function renderClarify(f) {
    const card = add("clarify");
    const opts = (f.options || [])
      .map((o, i) => `<button class="cl-opt" data-i="${i + 1}">${esc(o)}</button>`)
      .join("");
    card.innerHTML =
      `<div class="cl-q">${esc(f.question)}</div><div class="cl-opts">${opts}</div>` +
      `<div class="cl-free"><input type="text" placeholder="or type your own answer"><button class="cl-send">Send</button></div>`;
    card.querySelectorAll(".cl-opt").forEach((b) => {
      b.onclick = () => {
        post("/chat/clarify", { index: Number(b.dataset.i), text: b.textContent });
        card.querySelector(".cl-opts").innerHTML = `<span class="ar-result">${esc(b.textContent)}</span>`;
        card.querySelector(".cl-free").remove();
      };
    });
    const freeInput = card.querySelector(".cl-free input");
    card.querySelector(".cl-send").onclick = () => {
      const v = freeInput.value.trim();
      if (!v) return;
      post("/chat/clarify", { free_text: true, text: v });
      card.querySelector(".cl-opts").innerHTML = `<span class="ar-result">${esc(v)}</span>`;
      card.querySelector(".cl-free").remove();
    };
  }

  // ----- network -----
  async function post(url, body) {
    try {
      const r = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      return await r.json();
    } catch (e) {
      add("error", "network error: " + esc(e.message));
      return { ok: false };
    }
  }

  async function send() {
    const text = textarea.value.trim();
    if (!text) return;
    textarea.value = "";
    const ack = await post("/chat/send", { text });
    if (ack && ack.ok === false) {
      add("notice", ack.reason || "could not send");
      setBusy(false);
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    send();
  });
  textarea.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });
  newBtn.addEventListener("click", async () => {
    transcript.innerHTML = "";
    assistantEl = null;
    await post("/chat/new", {});
  });

  // ----- activity dashboard (Slice 3) -----
  const actTokens = document.getElementById("act-tokens");
  const actMinions = document.getElementById("act-minions");
  const actMinionCount = document.getElementById("act-minion-count");
  const actFiles = document.getElementById("act-files");
  const actFileCount = document.getElementById("act-file-count");

  const fmt = (n) => (n == null ? "0" : Number(n).toLocaleString());

  function renderDashboard(d) {
    if (!d) return;
    const t = d.tokens || {};
    const cache = t.cache_pct == null ? "" : ` · cache ${t.cache_pct}%`;
    const budget = t.budget_pct == null ? "" : ` · ${t.budget_pct}% of budget`;
    actTokens.innerHTML =
      `<div class="big">${fmt(t.total)}<span class="unit"> tok this session</span></div>` +
      `<div class="dim">in ${fmt(t.input)} · out ${fmt(t.output)}${cache}</div>` +
      `<div class="dim">project ${fmt(t.project_total)}${budget}</div>`;

    const sa = d.sub_agents || {};
    const active = sa.active || [];
    actMinionCount.textContent = String(active.length);
    if (!active.length) {
      const ran = sa.spawns ? `<span class="dim">${sa.spawns} spawned · ${fmt(sa.tokens)} tok</span>` : `<span class="dim">no sub-agents yet</span>`;
      actMinions.innerHTML = ran;
    } else {
      actMinions.innerHTML = active
        .map(
          (m) =>
            `<div class="minion-card"><div class="mc-head">🐙 <b>${esc(m.spawn_id)}</b> <span class="badge">${esc(m.tier)}</span></div>` +
            `<div class="dim mono">${esc(m.model)}</div>` +
            `<div class="mc-obj">${esc(m.objective || "(no objective)")}</div>` +
            `<div class="dim">round-trips ${m.round_trips}/${m.cap} · ${m.turns_used} turns</div></div>`
        )
        .join("");
    }

    const files = d.files || [];
    actFileCount.textContent = String(files.length);
    actFiles.innerHTML = files.length
      ? files
          .map((f) => `<div class="file-row"><span class="fa fa-${esc(f.action)}">${esc(f.action)}</span> <span class="mono">${esc(f.path)}</span></div>`)
          .join("")
      : `<span class="dim">none yet</span>`;
  }

  async function pollDashboard() {
    try {
      const r = await fetch("/chat/status");
      renderDashboard(await r.json());
    } catch (e) {
      /* transient; next tick retries */
    }
  }
  // Poll faster while a turn is active (minions/tokens move), slow when idle.
  setInterval(() => {
    if (sendBtn.disabled || document.querySelector(".minion-card")) pollDashboard();
  }, 1800);
  setInterval(pollDashboard, 6000);
  pollDashboard();

  // ----- event stream -----
  const qs = window.JAC_RESUME ? "?session=" + encodeURIComponent(window.JAC_RESUME) : "";
  const es = new EventSource("/chat/stream" + qs);
  es.onmessage = (ev) => {
    try {
      handle(JSON.parse(ev.data));
    } catch (e) {}
  };
  es.onerror = () => {
    /* EventSource auto-reconnects; the persistent server consumer buffers. */
  };

  textarea.focus();
})();
