(() => {
  "use strict";

  const SAVED_KEY = "vr_saved";
  const uuid = () =>
    (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2));

  const postEvent = (event_type, opts = {}) => {
    const body = {
      client_event_id: uuid(),
      schema_version: "1",
      event_type,
      edition_id: opts.edition_id || null,
      edition_item_id: opts.edition_item_id || null,
      ref: "web",
      client_at: new Date().toISOString(),
    };
    fetch("/api/v1/events", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      keepalive: true,
    }).catch(() => {});
  };

  const readSaved = () => {
    try {
      const raw = localStorage.getItem(SAVED_KEY);
      return raw ? JSON.parse(raw) : [];
    } catch (e) {
      return [];
    }
  };

  const writeSaved = (items) => {
    try {
      localStorage.setItem(SAVED_KEY, JSON.stringify(items));
    } catch (e) {
      /* 存储不可用时静默降级 */
    }
  };

  const isSaved = (productId) => readSaved().some((i) => i.product_id === productId);

  const toggleSave = (btn) => {
    const productId = btn.dataset.productId;
    const entry = {
      product_id: productId,
      slug: btn.dataset.slug,
      name: btn.dataset.name,
      one_liner: btn.dataset.oneLiner,
      edition_item_id: btn.dataset.itemId,
      edition_date: btn.dataset.editionDate,
      saved_at: new Date().toISOString(),
    };
    let items = readSaved();
    if (isSaved(productId)) {
      items = items.filter((i) => i.product_id !== productId);
      writeSaved(items);
      btn.classList.remove("saved");
      btn.textContent = "收藏";
      postEvent("unsave", { edition_item_id: entry.edition_item_id });
    } else {
      items = items.filter((i) => i.product_id !== productId);
      items.push(entry);
      writeSaved(items);
      btn.classList.add("saved");
      btn.textContent = "已收藏";
      postEvent("save", { edition_item_id: entry.edition_item_id });
    }
  };

  const setStatus = (itemId, text, cls) => {
    const el = document.querySelector(`[data-status-for="${itemId}"]`);
    if (!el) return;
    el.textContent = text;
    el.classList.remove("ok", "error");
    if (cls) el.classList.add(cls);
  };

  const RATING_LABEL = {
    interest: "已记录：有趣",
    boring: "已记录：无聊",
    try_failed: "已记录：没玩起来",
  };

  const describeState = (state) => {
    const parts = [];
    if (state.tried) parts.push("已记录：玩过了");
    if (state.rating_state && RATING_LABEL[state.rating_state]) parts.push(RATING_LABEL[state.rating_state]);
    if (state.failure_reason) parts.push("原因已记录");
    return parts.join(" · ");
  };

  const putFeedback = async (itemId, payload) => {
    const res = await fetch(`/api/v1/feedback/${itemId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      let message = "保存失败，请重试";
      try {
        const data = await res.json();
        if (data && data.message) message = data.message;
      } catch (e) { /* ignore */ }
      throw new Error(message);
    }
    return res.json();
  };

  const handleFeedbackClick = async (btn) => {
    const itemId = btn.dataset.itemId;
    const action = btn.dataset.action;
    try {
      let state;
      if (action === "tried") {
        state = await putFeedback(itemId, { tried: true });
      } else if (action === "interest" || action === "boring") {
        state = await putFeedback(itemId, { rating_state: action });
      } else if (action === "try-failed") {
        const box = document.querySelector(`[data-reasons-for="${itemId}"]`);
        if (box) box.hidden = false;
        state = await putFeedback(itemId, { rating_state: "try_failed" });
      } else if (action === "submit-fail") {
        const select = document.querySelector(`[data-reason-select="${itemId}"]`);
        const reason = select && select.value ? select.value : null;
        state = await putFeedback(itemId, {
          rating_state: "try_failed",
          failure_reason: reason,
        });
      } else if (action === "clear-rating") {
        state = await putFeedback(itemId, { rating_state: null });
        if (state.tried !== true) state = { ...state, tried: null };
      } else {
        return;
      }
      setStatus(itemId, describeState(state) || "已保存", "ok");
      document
        .querySelectorAll(`[data-item-id="${itemId}"][data-action]`)
        .forEach((el) => el.classList.remove("active"));
      if (state.tried) {
        const triedBtn = document.querySelector(`[data-action="tried"][data-item-id="${itemId}"]`);
        if (triedBtn) triedBtn.classList.add("active");
      }
      if (state.rating_state) {
        const active = document.querySelector(
          `[data-action="${state.rating_state === "try_failed" ? "try-failed" : state.rating_state}"][data-item-id="${itemId}"]`
        );
        if (active) active.classList.add("active");
      }
    } catch (err) {
      setStatus(itemId, `${err.message}（你的选择未丢失，可再次点击提交）`, "error");
    }
  };

  const restoreFeedbackStates = () => {
    const cards = document.querySelectorAll("[data-item-id]");
    const seen = new Set();
    cards.forEach((card) => {
      const itemId = card.dataset.itemId;
      if (!itemId || seen.has(itemId)) return;
      seen.add(itemId);
      fetch(`/api/v1/feedback/${itemId}`, { headers: { Accept: "application/json" } })
        .then((res) => (res.ok ? res.json() : null))
        .then((state) => {
          if (!state || !state.actor_available) return;
          const text = describeState(state);
          if (text) {
            setStatus(itemId, text, "ok");
            if (state.tried) {
              const triedBtn = document.querySelector(`[data-action="tried"][data-item-id="${itemId}"]`);
              if (triedBtn) triedBtn.classList.add("active");
            }
            if (state.rating_state) {
              const active = document.querySelector(
                `[data-action="${state.rating_state === "try_failed" ? "try-failed" : state.rating_state}"][data-item-id="${itemId}"]`
              );
              if (active) active.classList.add("active");
            }
          }
        })
        .catch(() => {});
    });
  };

  const trackViews = () => {
    const editionEl = document.querySelector("[data-edition-id]");
    if (editionEl) {
      const editionId = editionEl.dataset.editionId;
      let timer = setTimeout(() => {
        if (document.visibilityState === "visible") {
          postEvent("edition_view", { edition_id: editionId });
        }
      }, 1000);
      document.addEventListener("visibilitychange", () => {
        if (document.visibilityState !== "visible") {
          clearTimeout(timer);
        }
      });
    }
    const cards = document.querySelectorAll("[data-item-id]");
    if (!cards.length || !("IntersectionObserver" in window)) return;
    const appeared = new Map();
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          const el = entry.target;
          const itemId = el.dataset.itemId;
          if (entry.intersectionRatio >= 0.5) {
            if (!appeared.has(itemId)) {
              appeared.set(
                itemId,
                setTimeout(() => {
                  postEvent("card_impression", {
                    edition_item_id: itemId,
                    edition_id: el.dataset.editionId || null,
                  });
                }, 1000)
              );
            }
          } else if (appeared.has(itemId)) {
            clearTimeout(appeared.get(itemId));
            appeared.delete(itemId);
          }
        });
      },
      { threshold: [0.5] }
    );
    cards.forEach((card) => observer.observe(card));
  };

  const renderSaved = () => {
    const list = document.getElementById("saved-list");
    if (!list) return;
    const items = readSaved();
    const empty = document.getElementById("saved-empty");
    if (!items.length) {
      if (empty) empty.hidden = false;
      return;
    }
    if (empty) empty.hidden = true;
    items
      .slice()
      .sort((a, b) => (a.saved_at < b.saved_at ? 1 : -1))
      .forEach((item) => {
        const card = document.createElement("article");
        card.className = "card";
        const title = document.createElement("h3");
        title.className = "item-title";
        const link = document.createElement("a");
        link.href = `/p/${item.slug}`;
        link.textContent = item.name;
        title.appendChild(link);
        const desc = document.createElement("p");
        desc.className = "one-liner";
        desc.textContent = item.one_liner || "";
        const meta = document.createElement("p");
        meta.className = "muted small";
        meta.textContent = item.edition_date ? `收藏自 ${item.edition_date} 期` : "收藏条目";
        const actions = document.createElement("div");
        actions.className = "saved-actions";
        if (item.edition_item_id) {
          const feedback = document.createElement("a");
          feedback.className = "btn";
          feedback.href = `/feedback/${item.edition_item_id}`;
          feedback.textContent = "反馈";
          actions.appendChild(feedback);
        }
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "btn";
        remove.textContent = "取消收藏";
        remove.addEventListener("click", () => {
          writeSaved(readSaved().filter((i) => i.product_id !== item.product_id));
          card.remove();
          if (!readSaved().length && empty) empty.hidden = false;
        });
        actions.appendChild(remove);
        card.append(title, desc, meta, actions);
        list.appendChild(card);
      });
  };

  const initIssueForm = () => {
    const form = document.getElementById("issue-form");
    if (!form) return;
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const status = document.getElementById("issue-status");
      const data = new FormData(form);
      try {
        const res = await fetch("/api/v1/issues", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            edition_item_id: form.dataset.itemId,
            issue_type: data.get("issue_type"),
            note: data.get("note") || "",
          }),
        });
        if (!res.ok) throw new Error("提交失败");
        if (status) {
          status.textContent = "已收到报错，内容问题当日进入复核。";
          status.className = "feedback-status ok";
        }
        form.reset();
      } catch (e) {
        if (status) {
          status.textContent = "提交失败，请稍后重试。";
          status.className = "feedback-status error";
        }
      }
    });
  };

  document.addEventListener("click", (ev) => {
    const btn = ev.target.closest("button[data-action]");
    if (!btn) return;
    if (btn.dataset.action === "save") {
      toggleSave(btn);
      return;
    }
    if (btn.dataset.itemId) handleFeedbackClick(btn);
  });

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("button[data-action='save']").forEach((btn) => {
      if (isSaved(btn.dataset.productId)) {
        btn.classList.add("saved");
        btn.textContent = "已收藏";
      }
    });
    restoreFeedbackStates();
    trackViews();
    renderSaved();
    initIssueForm();
  });
})();
