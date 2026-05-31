// JAC web UI — chrome behavior shared by every page (the Console + the Control
// Panel). Three concerns, all vanilla so there's no build step:
//   1. left-rail collapse (persisted)
//   2. the management drawer (open on a nav click, close on scrim/✕/Esc, expand)
//   3. marking the active nav item
// HTMX does the actual fragment loading (hx-get on the nav buttons → #drawer-body);
// this file only drives the show/hide chrome around it.
(() => {
  "use strict";

  // ----- left rail collapse -----
  const RAIL_KEY = "jac.rail.collapsed";
  const railToggle = document.getElementById("rail-toggle");
  const setRail = (on) => {
    document.body.classList.toggle("rail-collapsed", on);
    try { localStorage.setItem(RAIL_KEY, on ? "1" : "0"); } catch (e) {}
  };
  try { if (localStorage.getItem(RAIL_KEY) === "1") document.body.classList.add("rail-collapsed"); } catch (e) {}
  if (railToggle) railToggle.addEventListener("click", () => setRail(!document.body.classList.contains("rail-collapsed")));

  // ----- drawer -----
  const body = document.body;
  const title = document.getElementById("drawer-title");
  const drawerBody = document.getElementById("drawer-body");
  const expandBtn = document.getElementById("drawer-expand");
  let activeOpener = null;

  function openDrawer(opener) {
    if (opener) {
      activeOpener = opener;
      if (opener.dataset.title && title) title.textContent = opener.dataset.title;
      markActive(opener);
    }
    body.classList.add("drawer-open");
  }
  function closeDrawer() {
    body.classList.remove("drawer-open", "drawer-wide");
    markActive(null);
    activeOpener = null;
  }
  function markActive(el) {
    document.querySelectorAll(".nav-item.on").forEach((n) => n.classList.remove("on"));
    if (el && el.classList.contains("nav-item")) el.classList.add("on");
  }

  // Open on any element that asked to (nav items, the doctor button, …). The
  // hx-get on the same element loads the fragment into #drawer-body.
  document.addEventListener("click", (e) => {
    const opener = e.target.closest("[data-drawer-open]");
    if (opener) { openDrawer(opener); return; }
    if (e.target.closest("[data-drawer-close]")) closeDrawer();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && body.classList.contains("drawer-open")) closeDrawer();
  });
  if (expandBtn) expandBtn.addEventListener("click", () => body.classList.toggle("drawer-wide"));

  // A fragment can ask to reuse the drawer chrome after an htmx swap (e.g. set a
  // fresh title, or signal "I'm a wide editor"). It does so via data-attrs on the
  // swapped-in root, read here.
  document.body.addEventListener("htmx:afterSwap", (e) => {
    if (e.target !== drawerBody) return;
    const root = drawerBody.firstElementChild;
    if (root && root.dataset && root.dataset.drawerTitle && title) {
      title.textContent = root.dataset.drawerTitle;
    }
    if (root && root.dataset && root.dataset.drawerWide !== undefined) {
      body.classList.add("drawer-wide");
    }
  });

  // ----- top-bar profile / model switcher -----
  // Switches the live agent: posts to /chat/switch-* which rebuilds Gru with a
  // snapshot/rollback guard, so a missing key leaves the running agent untouched.
  const esc = (s) =>
    String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
    );
  const jget = async (u) => (await fetch(u)).json();
  const jpost = async (u, b) =>
    (await fetch(u, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) })).json();

  const pBtn = document.getElementById("tb-profile-btn");
  const pMenu = document.getElementById("tb-profile-menu");
  const mBtn = document.getElementById("tb-model-btn");
  const mMenu = document.getElementById("tb-model-menu");
  const pName = document.getElementById("tb-profile-name");
  const mName = document.getElementById("tb-model-name");

  const closeMenus = () => {
    if (pMenu) pMenu.hidden = true;
    if (mMenu) mMenu.hidden = true;
  };
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".tb-switch")) closeMenus();
  });

  const applyOk = (r) => {
    if (mName && r.model) mName.textContent = r.model;
    if (pName && r.profile) pName.textContent = r.profile;
  };
  const menuError = (menu, msg) => {
    menu.innerHTML = `<div class="tb-menu-empty" style="color:var(--danger)">${esc(msg)}</div>`;
  };
  const onResult = (menu, r) => {
    if (r && r.ok) {
      applyOk(r);
      closeMenus();
    } else {
      menuError(menu, (r && r.reason) || "switch failed");
    }
  };

  async function openProfileMenu() {
    if (!pMenu) return;
    closeMenus();
    const d = await jget("/chat/switcher");
    pMenu.innerHTML = (d.profiles || []).length
      ? d.profiles
          .map(
            (p) =>
              `<button class="tb-opt ${p === d.current_profile ? "on" : ""}" data-p="${esc(p)}">${esc(p)}</button>`
          )
          .join("")
      : `<div class="tb-menu-empty">No profiles — add one under Profiles.</div>`;
    pMenu.hidden = false;
    pMenu.querySelectorAll(".tb-opt").forEach((b) => {
      b.onclick = async () => onResult(pMenu, await jpost("/chat/switch-profile", { profile: b.dataset.p }));
    });
  }

  async function openModelMenu() {
    if (!mMenu) return;
    closeMenus();
    const d = await jget("/chat/switcher");
    let html = "";
    const models = d.models || [];
    if (models.length) {
      const tiers = {};
      models.forEach((m) => (tiers[m.tier] = tiers[m.tier] || []).push(m.model));
      for (const t of Object.keys(tiers)) {
        html += `<div class="tb-group">${esc(t)}</div>`;
        html += tiers[t]
          .map(
            (m) =>
              `<button class="tb-opt ${m === d.current_model ? "on" : ""}" data-m="${esc(m)}">${esc(m)}</button>`
          )
          .join("");
      }
    } else {
      html = `<div class="tb-menu-empty">No profile models — pick a profile first.</div>`;
    }
    html += `<div class="tb-adhoc"><input type="text" id="tb-adhoc-input" placeholder="provider:model-id"><button class="small" id="tb-adhoc-go">Use</button></div>`;
    mMenu.innerHTML = html;
    mMenu.hidden = false;
    mMenu.querySelectorAll(".tb-opt").forEach((b) => {
      b.onclick = async () => onResult(mMenu, await jpost("/chat/switch-model", { model: b.dataset.m }));
    });
    const inp = document.getElementById("tb-adhoc-input");
    const go = document.getElementById("tb-adhoc-go");
    const submit = async () => {
      const v = inp.value.trim();
      if (v) onResult(mMenu, await jpost("/chat/switch-model", { model: v }));
    };
    if (go) go.onclick = submit;
    if (inp)
      inp.onkeydown = (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          submit();
        }
      };
  }

  if (pBtn)
    pBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      pMenu.hidden ? openProfileMenu() : closeMenus();
    });
  if (mBtn)
    mBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      mMenu.hidden ? openModelMenu() : closeMenus();
    });

  // ----- readiness dot (top bar) -----
  (async () => {
    const dot = document.getElementById("tb-dot");
    if (!dot) return;
    try {
      const { status } = await jget("/doctor.json");
      dot.classList.add(status === "bad" ? "bad" : status === "warn" ? "warn" : "ok");
    } catch (e) {
      /* leave neutral */
    }
  })();
})();
