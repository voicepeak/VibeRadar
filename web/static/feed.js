(() => {
  "use strict";
  const words = window.VR_I18N || {};
  const t = (key, vars = {}) => Object.entries(vars).reduce((s, [k,v]) => s.replace(`{${k}}`, v), words[key] || key);
  const lang = document.documentElement.lang === "en" ? "en" : "zh";
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const status = document.getElementById("refresh-status");
  const feed = document.getElementById("feed-sections");
  const form = document.getElementById("refresh-form");
  const initSaved = () => {
    let saved;
    try { saved = JSON.parse(localStorage.getItem("vr_saved") || "[]"); } catch { saved = []; }
    document.querySelectorAll('[data-action="save"]').forEach(button => {
      const active = saved.some(x => x.product_id === button.dataset.productId);
      button.classList.toggle("saved", active);
      if (active) button.textContent = words["card.saved"] || "Saved";
    });
  };
  const updateTime = raw => {
    const el = document.getElementById("feed-updated");
    if (el && raw) el.textContent = t("feed.updated") + " " + new Intl.DateTimeFormat(lang === "zh" ? "zh-CN" : "en-GB", {
      timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"
    }).format(new Date(raw));
  };
  const showRun = run => {
    status.textContent = run.budget_exhausted ? t("feed.budget") : run.status === "failed" ? t("feed.failed") :
      run.status === "running" ? t("feed.running") : run.new_items ? t("feed.added", {count: run.new_items}) : t("feed.no_changes");
    updateTime(run.finished_at || run.started_at);
  };
  const replaceFeed = async () => {
    const res = await fetch(`/api/v1/feed?lang=${lang}`);
    if (!res.ok) throw new Error("feed");
    const data = await res.json();
    feed.innerHTML = data.html;
    initSaved();
    const welcome = document.querySelector(".feed-welcome");
    if (welcome && data.total) welcome.remove();
    const total = document.querySelector(".feed-nav > span");
    if (total) total.textContent = t("feed.total", {count: data.total});
  };
  const poll = async id => {
    if (form) form.querySelector("button").disabled = true;
    status.textContent = t("feed.running");
    try {
      // Bounded wait; a reload can resume the same refresh run.
      for (let i = 0; i < 300; i++) {
        await sleep(2000);
        const res = await fetch(`/api/v1/refresh/${encodeURIComponent(id)}`);
        if (!res.ok) throw new Error("status");
        const run = await res.json();
        showRun(run);
        if (run.status !== "running") {
          await replaceFeed();
          return;
        }
      }
      status.textContent = t("feed.wait");
    } catch { status.textContent = t("feed.failed"); }
    finally { if (form) form.querySelector("button").disabled = false; }
  };
  if (form) form.addEventListener("submit", async ev => {
    ev.preventDefault();
    form.querySelector("button").disabled = true;
    try {
      const res = await fetch("/api/v1/refresh", {method: "POST"});
      if (!res.ok) throw new Error("refresh");
      const data = await res.json();
      if (data.status === "cooldown") status.textContent = t("feed.cooldown");
      else if (data.run_id) await poll(data.run_id);
    } catch { status.textContent = t("feed.failed"); }
    finally { form.querySelector("button").disabled = false; }
  });
  if (feed?.dataset.runId) poll(feed.dataset.runId);
  document.addEventListener("click", async ev => {
    const link = ev.target.closest("[data-feed-next]");
    if (!link) return;
    ev.preventDefault();
    if (link.dataset.loading) return;
    link.dataset.loading = "true";
    try {
      const res = await fetch(`/api/v1/feed?cursor=${encodeURIComponent(link.dataset.feedNext)}&lang=${lang}`);
      if (!res.ok) throw new Error("page");
      const data = await res.json();
      const doc = new DOMParser().parseFromString(data.html, "text/html");
      const existing = new Set([...feed.querySelectorAll("[data-item-id]")].map(x => x.dataset.itemId));
      doc.querySelectorAll("#all .item-card").forEach(card => {
        if (!existing.has(card.dataset.itemId)) feed.querySelector("#all").append(card);
      });
      const next = doc.querySelector("[data-feed-next]");
      if (next) link.replaceWith(next); else link.remove();
      initSaved();
    } catch { link.dataset.loading = ""; status.textContent = t("feed.failed"); }
  });
  document.querySelectorAll(".site-nav > a").forEach(a => {
    if (a.pathname === location.pathname) a.setAttribute("aria-current", "page");
  });

  const launch = document.getElementById("trial-launch");
  if (launch) launch.addEventListener("submit", async ev => {
    ev.preventDefault();
    const button = launch.querySelector("button");
    const line = document.getElementById("trial-launch-status");
    button.disabled = true;
    line.textContent = t("trial.state.preparing");
    try {
      const res = await fetch(`/api/v1/trials/${encodeURIComponent(launch.dataset.slug)}`, {method: "POST"});
      if (!res.ok) throw new Error("launch");
      const data = await res.json();
      location.assign(`/trial/${encodeURIComponent(data.session_id)}?lang=${lang}`);
    } catch { line.textContent = t("trial.failed"); button.disabled = false; }
  });
  const terminal = document.getElementById("trial-terminal");
  if (!terminal) return;
  const sessionId = terminal.dataset.sessionId;
  const logs = document.getElementById("trial-logs");
  document.getElementById("copy-logs").addEventListener("click", async ev => {
    try { await navigator.clipboard.writeText(logs.textContent); ev.target.textContent = t("trial.copied"); }
    catch { ev.target.textContent = t("trial.copy_failed"); }
  });
  const elapsed = document.getElementById("trial-elapsed");
  const clock = setInterval(() => { elapsed.textContent = t("trial.elapsed", {seconds: Math.max(0, Math.floor((Date.now()-Date.parse(elapsed.dataset.started))/1000))}); }, 1000);
  const seen = new Set();
  // Polling adds logs via textContent, so third-party output is never interpreted as HTML.
  let first = true;
  const tick = async () => {
    try {
      const res = await fetch(`/api/v1/trial-sessions/${encodeURIComponent(sessionId)}`);
      if (!res.ok) throw new Error("trial");
      const data = await res.json();
      if (first) { logs.textContent = ""; first = false; }
      const atBottom = logs.scrollHeight - logs.scrollTop - logs.clientHeight < 48;
      data.events.forEach(e => {
        if (!seen.has(e.id)) { logs.textContent += `[${e.kind}] ${e.message}\n`; seen.add(e.id); }
      });
      if (atBottom) logs.scrollTop = logs.scrollHeight;
      document.getElementById("trial-state").textContent = t("trial.state."+data.status);
      const error = document.getElementById("trial-error");
      error.hidden = !data.error; error.textContent = data.error || "";
      const open = document.getElementById("trial-open");
      open.hidden = data.status !== "ready" || !data.url;
      if (data.url) open.href = data.url;
      const tried = document.getElementById("trial-tried");
      tried.hidden = !data.ready_at;
      tried.disabled = !data.evidence_available;
      if (["failed", "timeout", "stopped"].includes(data.status)) { clearInterval(clock); return; }
      setTimeout(tick, 2000);
    } catch { document.getElementById("trial-feedback-status").textContent = t("trial.failed"); setTimeout(tick, 5000); }
  };
  tick();
  document.getElementById("trial-tried").addEventListener("click", async ev => {
    ev.target.disabled = true;
    try {
      const res = await fetch(`/api/v1/trial-sessions/${encodeURIComponent(sessionId)}/tried`, {method: "POST"});
      if (!res.ok) throw new Error("tried");
      document.getElementById("trial-feedback-status").textContent = t("trial.confirmed");
    } catch { document.getElementById("trial-feedback-status").textContent = t("trial.failed"); ev.target.disabled = false; }
  });
})();
