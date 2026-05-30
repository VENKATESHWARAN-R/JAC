// JAC web chat (Slice 2/3). Vanilla JS: EventSource for the agent event stream,
// fetch for sending messages and resolving HITL approvals/clarifications, and a
// polled /chat/status for the activity sidebar.
(() => {
  "use strict";

  const transcript = document.getElementById("transcript");
  const form = document.getElementById("chat-form");
  const textarea = document.getElementById("chat-text");
  const sendBtn = document.getElementById("chat-send");
  const newBtn = document.getElementById("chat-new");
  const sessionLabel = document.getElementById("chat-session");
  const collapseBtn = document.getElementById("act-toggle");
  const layout = document.getElementById("chat-layout");

  let streamEl = null; // assistant bubble being streamed into (if TextDelta used)
  let lastToolEl = null; // last tool chip, for completion status
  let typingEl = null; // transient "thinking" indicator, always kept last

  // ----- DOM helpers -----
  const esc = (s) =>
    String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );

  const atBottom = () =>
    transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 100;

  function scroll() {
    transcript.scrollTop = transcript.scrollHeight;
  }

  // Insert a message element, keeping the typing indicator (if any) last.
  function add(cls, html) {
    const wasBottom = atBottom();
    const el = document.createElement("div");
    el.className = "msg " + cls;
    if (html !== undefined) el.innerHTML = html;
    if (typingEl) transcript.insertBefore(el, typingEl);
    else transcript.appendChild(el);
    if (wasBottom) scroll();
    return el;
  }

  function showTyping() {
    if (!typingEl) {
      typingEl = document.createElement("div");
      typingEl.className = "msg assistant typing";
      typingEl.innerHTML = "<span class='dots'>● ● ●</span>";
    }
    transcript.appendChild(typingEl); // always last
    scroll();
  }
  function hideTyping() {
    if (typingEl) {
      typingEl.remove();
      typingEl = null;
    }
  }

  function setBusy(busy) {
    sendBtn.disabled = busy;
    sendBtn.textContent = busy ? "…" : "Send";
    if (!busy) hideTyping();
  }

  // ----- frame dispatch -----
  function handle(f) {
    switch (f.type) {
      case "SessionStarted":
        sessionLabel.textContent = f.id || "—";
        break;
      case "UserMessage":
        streamEl = null;
        lastToolEl = null;
        add("user", esc(f.content));
        setBusy(true);
        showTyping();
        break;
      case "ModelRequestStarted":
        if (sendBtn.disabled) showTyping();
        break;
      case "TextDelta":
        if (!streamEl) {
          hideTyping();
          streamEl = add("assistant", "");
        }
        streamEl.textContent += f.content;
        if (atBottom()) scroll();
        break;
      case "ToolCallStarted": {
        const reason = f.reason ? " — " + esc(f.reason) : "";
        lastToolEl = add("tool", `🔧 <b>${esc(f.tool_name)}</b>${reason} <span class="tstat">running…</span>`);
        showTyping();
        break;
      }
      case "ToolCallCompleted":
        markTool("ok", "done");
        break;
      case "ToolCallFailed":
        markTool("err", "failed: " + esc(f.error));
        break;
      case "ApprovalRequest":
        hideTyping();
        renderApproval(f);
        break;
      case "ModeAutoDecision":
        add("notice", `mode <b>${esc(f.mode)}</b> auto-${esc(f.decision)}ed <b>${esc(f.tool_name)}</b>`);
        break;
      case "ClarifyRequest":
        hideTyping();
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
        hideTyping();
        if (streamEl) {
          if (f.output) streamEl.textContent = f.output;
        } else if (f.output) {
          add("assistant", esc(f.output)); // appended after any tool cards
        }
        streamEl = null;
        scroll();
        break;
      case "RunFailed":
        hideTyping();
        add("error", esc(f.error));
        streamEl = null;
        break;
      case "Error":
        hideTyping();
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
        break; // unknown frames are ignored, not fatal
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
      if (sendBtn.disabled) showTyping();
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
        if (sendBtn.disabled) showTyping();
      };
    });
    const freeInput = card.querySelector(".cl-free input");
    card.querySelector(".cl-send").onclick = () => {
      const v = freeInput.value.trim();
      if (!v) return;
      post("/chat/clarify", { free_text: true, text: v });
      card.querySelector(".cl-opts").innerHTML = `<span class="ar-result">${esc(v)}</span>`;
      card.querySelector(".cl-free").remove();
      if (sendBtn.disabled) showTyping();
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
    streamEl = null;
    typingEl = null;
    await post("/chat/new", {});
  });
  if (collapseBtn && layout) {
    collapseBtn.addEventListener("click", () => {
      const collapsed = layout.classList.toggle("collapsed");
      collapseBtn.textContent = collapsed ? "‹ activity" : "activity ›";
      try {
        localStorage.setItem("jac.activity.collapsed", collapsed ? "1" : "0");
      } catch (e) {}
    });
    try {
      if (localStorage.getItem("jac.activity.collapsed") === "1") {
        layout.classList.add("collapsed");
        collapseBtn.textContent = "‹ activity";
      }
    } catch (e) {}
  }

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
    actTokens.innerHTML =
      `<div class="big">${fmt(t.total)}<span class="unit"> tok</span></div>` +
      `<div class="dim">in ${fmt(t.input)} · out ${fmt(t.output)}${cache}</div>` +
      `<div class="dim">project ${fmt(t.project_total)}</div>`;

    const sa = d.sub_agents || {};
    const active = sa.active || [];
    actMinionCount.textContent = String(active.length);
    if (!active.length) {
      actMinions.innerHTML = sa.spawns
        ? `<span class="dim">${sa.spawns} spawned · ${fmt(sa.tokens)} tok</span>`
        : `<span class="dim">none active</span>`;
    } else {
      actMinions.innerHTML = active
        .map(
          (m) =>
            `<div class="minion-card"><div class="mc-head">🐙 <b>${esc(m.spawn_id)}</b> <span class="badge">${esc(m.tier)}</span></div>` +
            `<div class="mc-obj">${esc(m.objective || "(no objective)")}</div>` +
            `<div class="dim">${m.round_trips}/${m.cap} rt · ${m.turns_used} turns</div></div>`
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
    /* EventSource auto-reconnects; broadcast means each connection gets frames. */
  };

  textarea.focus();
})();
