(() => {
  function getActiveConvId() {
    try {
      // 1. TanStack Router state (official router of Antigravity 2.0)
      if (window.__TSR_ROUTER__?.state) {
        const matches = window.__TSR_ROUTER__.state.matches || [];
        for (let i = matches.length - 1; i >= 0; i--) {
          const cid = matches[i]?.params?.cascadeId;
          if (cid) return cid;
        }
        const pathname = window.__TSR_ROUTER__.state.location?.pathname || "";
        const parts = pathname.split("/");
        const idx = parts.indexOf("c");
        if (idx !== -1 && parts[idx + 1]) {
          return parts[idx + 1];
        }
      }

      // 2. Main chat view container in DOM
      const mainChat = document.querySelector("div:not([data-testid=\"conversation-row-sidebar\"])[data-cascade-id]");
      if (mainChat) {
        const id = mainChat.getAttribute("data-cascade-id");
        if (id) return id;
      }

      // 3. Currently selected row in sidebar
      const selRow = document.querySelector("[data-selected=\"true\"][data-cascade-id]") || document.querySelector("[data-selected=\"true\"]");
      if (selRow) {
        const id = selRow.getAttribute("data-cascade-id");
        if (id) return id;
      }

      // 4. Check window.location.pathname (/c/<id>)
      const parts = window.location.pathname.split("/");
      const idx = parts.indexOf("c");
      if (idx !== -1 && parts[idx + 1]) {
        return parts[idx + 1];
      }

      // 5. Any element with data-cascade-id inside main/chat area
      const anyChat = document.querySelector("[data-cascade-id]");
      if (anyChat) {
        const id = anyChat.getAttribute("data-cascade-id");
        if (id) return id;
      }
    } catch (_) {}
    return null;
  }

  function getStatusColor(pct, fiveHourPct) {
    if (pct >= 90 || (fiveHourPct != null && fiveHourPct <= 10)) {
      return { dot: "#ef4444", text: "#ef4444" };
    }
    if (pct >= 75 || (fiveHourPct != null && fiveHourPct <= 25)) {
      return { dot: "#f59e0b", text: "#f59e0b" };
    }
    return { dot: "#10b981", text: "#10b981" };
  }

  function fmtK(n) {
    if (!n || n <= 0) return "0";
    if (n >= 1000000) return (n / 1000000).toFixed(1) + "M";
    if (n >= 1000) return Math.round(n / 1000) + "k";
    return n + "";
  }

  function formatResetTime(seconds) {
    if (!seconds) return "";
    const sec = parseInt(seconds, 10);
    if (isNaN(sec)) return "";
    const diff = sec - Math.floor(Date.now() / 1000);
    if (diff <= 0) return "重置中";
    const days = Math.floor(diff / 86400);
    const hours = Math.floor((diff % 86400) / 3600);
    const mins = Math.floor((diff % 3600) / 60);
    if (days > 0) return `${days}d${hours}h`;
    if (hours > 0) return `${hours}h${mins}m`;
    return `${mins}m`;
  }

  async function fetchOfficialQuotas() {
    try {
      const btn = document.querySelector("button");
      if (!btn) return null;
      const fk = Object.keys(btn).find(k => k.startsWith("__reactFiber"));
      if (!fk) return null;
      let cur = btn[fk];
      let client = null;
      while (cur) {
        if (cur.memoizedProps?.value?.retrieveUserQuotaSummary) {
          client = cur.memoizedProps.value;
          break;
        }
        cur = cur.return;
      }
      if (!client) return null;
      const res = await Promise.race([
        client.retrieveUserQuotaSummary({}),
        new Promise(r => setTimeout(() => r(null), 2000))
      ]);
      return res?.response?.groups || null;
    } catch (e) {
      return null;
    }
  }

  let cachedQuotas = null;
  let lastQuotaFetch = 0;

  async function getQuotas() {
    const now = Date.now();
    if (!cachedQuotas || now - lastQuotaFetch > 4000) {
      lastQuotaFetch = now;
      const q = await fetchOfficialQuotas();
      if (q) cachedQuotas = q;
    }
    return cachedQuotas;
  }

  window.__AGY_RENDER__ = async function() {
    const data = window.__AGY_DATA__;
    if (!data) return;

    let container = document.getElementById("antigravity-token-widget");
    const settingsBtn = Array.from(document.querySelectorAll("button")).find(b => {
      const txt = (b.textContent || "").trim();
      const label = b.getAttribute("aria-label") || "";
      return (
        txt.includes("Settings") || txt.includes("Настройки") || txt.includes("设置") ||
        label.includes("Settings") || label.includes("Настройки") || label.includes("设置")
      );
    });

    if (!settingsBtn) return;

    const isCollapsed = localStorage.getItem("agy_hud_collapsed") === "true";

    if (!container) {
      container = document.createElement("div");
      container.id = "antigravity-token-widget";
      settingsBtn.parentElement.insertBefore(container, settingsBtn);
    }

    container.style.cssText = [
      isCollapsed ? "padding: 5px 8px" : "padding: 7px 9px 8px 9px",
      "margin: 2px 6px 4px 6px",
      "border-radius: 8px",
      "background: rgba(255, 255, 255, 0.03)",
      "border: 1px solid rgba(255, 255, 255, 0.07)",
      "backdrop-filter: blur(12px)",
      "-webkit-backdrop-filter: blur(12px)",
      "font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'PingFang SC', sans-serif",
      "color: rgba(255, 255, 255, 0.9)",
      "user-select: none",
      "cursor: pointer",
      "transition: border-color 0.2s ease, background 0.2s ease, box-shadow 0.2s ease",
      "box-shadow: 0 1px 4px rgba(0, 0, 0, 0.1)"
    ].join("; ");

    container.onmouseenter = () => {
      container.style.borderColor = "rgba(255, 255, 255, 0.18)";
      container.style.background = "rgba(255, 255, 255, 0.055)";
      container.style.boxShadow = "0 4px 12px rgba(0, 0, 0, 0.22)";
    };
    container.onmouseleave = () => {
      container.style.borderColor = "rgba(255, 255, 255, 0.07)";
      container.style.background = "rgba(255, 255, 255, 0.03)";
      container.style.boxShadow = "0 1px 4px rgba(0, 0, 0, 0.1)";
    };

    // Toggle expand/collapse on click (固定中文，点击纯粹负责伸缩)
    container.onclick = (e) => {
      e.stopPropagation();
      const nextCollapsed = localStorage.getItem("agy_hud_collapsed") === "true" ? "false" : "true";
      try {
        localStorage.setItem("agy_hud_collapsed", nextCollapsed);
      } catch (_) {}
      if (window.__AGY_RENDER__) window.__AGY_RENDER__();
    };

    // 1. Session Context from SQLite
    const activeId = getActiveConvId();
    let session = null;
    if (data.sessions && activeId && data.sessions[activeId]) {
      session = data.sessions[activeId];
    } else if (activeId && data.current_session && data.current_session.session_id === activeId) {
      session = data.current_session;
    } else if (!activeId && data.current_session) {
      session = data.current_session;
    } else {
      session = {
        session_id: activeId || "new",
        context_size: 0,
        max_context: 1000000,
        context_percent: 0.0,
        cached_tokens: 0,
        prompt_tokens: 0,
        output_tokens: 0,
        thinking_tokens: 0,
        text_tokens: 0
      };
    }

    const pct = (session.context_percent != null) ? session.context_percent : 0.0;
    const barWidth = Math.min(100, Math.max(0, pct));
    const ctxK = fmtK(session.context_size || 0);
    const maxK = ((session.max_context || 1000000) >= 1000000) ? "1M" : fmtK(session.max_context);

    // 2. Official Quota Summary from Antigravity Backend
    const quotaGroups = await getQuotas();
    let fiveHourPct = 100;
    let fiveHourReset = "";
    let weeklyPct = 100;
    let weeklyReset = "";

    if (quotaGroups && quotaGroups.length > 0) {
      const geminiGroup = quotaGroups.find(g => g.displayName?.includes("Gemini")) || quotaGroups[0];
      const hBucket = geminiGroup?.buckets?.find(b => b.window === "5h" || b.bucketId?.includes("5h"));
      const wBucket = geminiGroup?.buckets?.find(b => b.window === "weekly" || b.bucketId?.includes("weekly"));

      if (hBucket?.remaining?.value != null) {
        fiveHourPct = Math.round(hBucket.remaining.value * 100);
        fiveHourReset = formatResetTime(hBucket.resetTime?.seconds);
      }
      if (wBucket?.remaining?.value != null) {
        weeklyPct = Math.round(wBucket.remaining.value * 100);
        weeklyReset = formatResetTime(wBucket.resetTime?.seconds);
      }
    }

    const statusColor = getStatusColor(pct, fiveHourPct);

    const tip5h = `5小时配额: ${fiveHourPct}%${fiveHourReset ? ` (${fiveHourReset}后重置)` : ""}`;
    const tipWeekly = `每周配额: ${weeklyPct}%${weeklyReset ? ` (${weeklyReset}后重置)` : ""}`;
    let detailTokens = "";
    if (session.context_size > 0) {
      detailTokens = `\n上下文明细: 缓存 ${fmtK(session.cached_tokens || 0)} | 输入 ${fmtK(session.prompt_tokens || 0)} | 思考 ${fmtK(session.thinking_tokens || 0)} | 输出 ${fmtK(session.output_tokens || 0)}`;
    }
    const actionHint = isCollapsed ? "点击展开详情" : "点击收起面板";
    container.title = `${tip5h}\n${tipWeekly}${detailTokens}\n\n(${actionHint})`;

    // Render Compact (Collapsed) View
    if (isCollapsed) {
      container.innerHTML = `
        <div style="display: flex; align-items: center; justify-content: space-between; gap: 4px; line-height: 1.2;">
          <div style="display: flex; align-items: center; gap: 5px; min-width: 0;">
            <span style="display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: ${statusColor.dot}; box-shadow: 0 0 5px ${statusColor.dot}; flex-shrink: 0;"></span>
            <span style="font-size: 10.5px; font-weight: 500; color: #94a3b8; white-space: nowrap;">
              上下文 <span style="font-weight: 600; color: ${statusColor.text}; font-family: ui-monospace, Menlo, Consolas, monospace;">${pct.toFixed(1)}%</span>
            </span>
          </div>
          <div style="display: flex; align-items: center; gap: 6px; flex-shrink: 0;">
            <span style="font-size: 10px; font-weight: 500; color: #cbd5e1; font-family: ui-monospace, Menlo, Consolas, monospace;">5h:${fiveHourPct}%</span>
            <span style="font-size: 10px; font-weight: 500; color: #a78bfa; font-family: ui-monospace, Menlo, Consolas, monospace;">周:${weeklyPct}%</span>
            <svg style="width: 10px; height: 10px; fill: none; stroke: currentColor; color: #64748b;" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M19 9l-7 7-7-7"></path>
            </svg>
          </div>
        </div>
      `;
      return;
    }

    // Render Expanded (Full) View
    let detailTokensHtml = "";
    if (session.context_size > 0) {
      detailTokensHtml = `
        <div style="display: flex; justify-content: space-between; font-size: 8.5px; color: #64748b; font-family: ui-monospace, Menlo, Consolas, monospace; margin-top: 2.5px; line-height: 1;">
          <span>缓 ${fmtK(session.cached_tokens || 0)}</span>
          <span>入 ${fmtK(session.prompt_tokens || 0)}</span>
          <span>思 ${fmtK(session.thinking_tokens || 0)}</span>
          <span>出 ${fmtK(session.output_tokens || 0)}</span>
        </div>
      `;
    }

    container.innerHTML = `
      <!-- 1. 会话上下文 (首行整合状态与收起按键) -->
      <div style="margin-bottom: 6px;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 3px; line-height: 1.2;">
          <div style="display: flex; align-items: center; gap: 4.5px;">
            <span style="display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: ${statusColor.dot}; box-shadow: 0 0 5px ${statusColor.dot}; flex-shrink: 0;"></span>
            <span style="font-size: 10px; color: #cbd5e1; font-weight: 500;">上下文</span>
            <span style="font-size: 10px; font-weight: 600; color: ${statusColor.text}; font-family: ui-monospace, Menlo, Consolas, monospace;">${pct.toFixed(1)}%</span>
            <span style="color: #64748b; font-size: 9px; font-family: ui-monospace, Menlo, Consolas, monospace;">(${ctxK}/${maxK})</span>
          </div>
          <div style="display: flex; align-items: center; gap: 2px; color: #64748b; font-size: 9.5px; padding: 1px 4px; border-radius: 3px; background: rgba(255, 255, 255, 0.03);">
            <span>收起</span>
            <svg style="width: 9px; height: 9px; fill: none; stroke: currentColor; transform: rotate(180deg);" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M19 9l-7 7-7-7"></path>
            </svg>
          </div>
        </div>
        <div style="background: rgba(255,255,255,0.06); height: 4px; border-radius: 999px; overflow: hidden;">
          <div style="background: linear-gradient(90deg, #10b981, #06b6d4); width: ${barWidth}%; height: 100%; border-radius: 999px; transition: width 0.3s ease;"></div>
        </div>
        ${detailTokensHtml}
      </div>

      <!-- 2. 配额并排双列 (5小时配额 & 每周配额) -->
      <div style="display: flex; gap: 8px;">
        <!-- 5小时配额 -->
        <div style="flex: 1; min-width: 0;">
          <div style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 2.5px; line-height: 1.2;">
            <span style="font-size: 9.5px; color: #94a3b8; font-weight: 500;">5h配额</span>
            <span style="font-size: 9.5px; font-weight: 600; color: #38bdf8; font-family: ui-monospace, Menlo, Consolas, monospace;">
              ${fiveHourPct}%${fiveHourReset ? `<span style="font-size: 8.5px; color: #64748b; font-weight: 400; margin-left: 2px;">(${fiveHourReset})</span>` : ""}
            </span>
          </div>
          <div style="background: rgba(255,255,255,0.06); height: 3.5px; border-radius: 999px; overflow: hidden;">
            <div style="background: linear-gradient(90deg, #38bdf8, #818cf8); width: ${fiveHourPct}%; height: 100%; border-radius: 999px; transition: width 0.3s ease;"></div>
          </div>
        </div>

        <!-- 每周配额 -->
        <div style="flex: 1; min-width: 0;">
          <div style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 2.5px; line-height: 1.2;">
            <span style="font-size: 9.5px; color: #94a3b8; font-weight: 500;">周配额</span>
            <span style="font-size: 9.5px; font-weight: 600; color: #a78bfa; font-family: ui-monospace, Menlo, Consolas, monospace;">
              ${weeklyPct}%${weeklyReset ? `<span style="font-size: 8.5px; color: #64748b; font-weight: 400; margin-left: 2px;">(${weeklyReset})</span>` : ""}
            </span>
          </div>
          <div style="background: rgba(255,255,255,0.06); height: 3.5px; border-radius: 999px; overflow: hidden;">
            <div style="background: linear-gradient(90deg, #a78bfa, #c084fc); width: ${weeklyPct}%; height: 100%; border-radius: 999px; transition: width 0.3s ease;"></div>
          </div>
        </div>
      </div>
    `;
  };

  if (!window.__AGY_LISTENER_SET__) {
    window.__AGY_LISTENER_SET__ = true;
    let lastId = null;
    let lastPath = window.location.pathname;

    if (window.__TSR_ROUTER__ && typeof window.__TSR_ROUTER__.subscribe === "function") {
      try {
        window.__TSR_ROUTER__.subscribe(() => {
          if (window.__AGY_RENDER__) window.__AGY_RENDER__();
        });
      } catch (_) {}
    }

    setInterval(() => {
      const currentId = getActiveConvId();
      const currentPath = window.location.pathname;
      if (currentId !== lastId || currentPath !== lastPath) {
        lastId = currentId;
        lastPath = currentPath;
        if (window.__AGY_RENDER__) window.__AGY_RENDER__();
      }
    }, 100);
  }

  if (window.__AGY_RENDER__) {
    window.__AGY_RENDER__();
  }
})();
