(() => {
  const params = new URLSearchParams(location.search);
  let token = params.get("token") || localStorage.getItem("clob_dash_token") || "";

  const $ = (id) => document.getElementById(id);

  function fmtUsd(n, digs = 2) {
    if (n == null || Number.isNaN(Number(n))) return "—";
    const v = Number(n);
    const sign = v < 0 ? "-" : "";
    return `${sign}$${Math.abs(v).toLocaleString(undefined, {
      minimumFractionDigits: digs,
      maximumFractionDigits: digs,
    })}`;
  }

  function fmtPct(n) {
    if (n == null || Number.isNaN(Number(n))) return "—";
    return `${(Number(n) * 100).toFixed(1)}¢`;
  }

  function shortSlug(s) {
    if (!s) return "unknown";
    return String(s).replace(/-/g, " ").slice(0, 64);
  }

  function ago(ts) {
    if (!ts) return "never";
    const t = Date.parse(ts);
    if (!t) return ts;
    const s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 60) return `${Math.floor(s)}s ago`;
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
    return `${Math.floor(s / 86400)}d ago`;
  }

  async function api(path, opts = {}) {
    const headers = { ...(opts.headers || {}) };
    // Prefer query token — some Render edges 404 Bearer-only browser calls.
    let url = path;
    if (token) {
      const join = path.includes("?") ? "&" : "?";
      url = `${path}${join}token=${encodeURIComponent(token)}`;
    }
    const res = await fetch(url, { ...opts, headers });
    if (res.status === 401) {
      showTokenGate();
      throw new Error("unauthorized");
    }
    return res.json();
  }

  function showTokenGate() {
    const main = $("main");
    main.innerHTML = `
      <div class="token-gate">
        <h1 style="margin:0;font-size:28px;letter-spacing:-0.03em">CLOB MM</h1>
        <p class="muted" style="margin:0">Enter dashboard token to view live ledger.</p>
        <input id="token-input" type="password" placeholder="DASHBOARD_TOKEN" autocomplete="current-password" />
        <button id="token-save" type="button">Open portfolio</button>
      </div>`;
    $("token-save").onclick = () => {
      token = $("token-input").value.trim();
      localStorage.setItem("clob_dash_token", token);
      location.href = token ? `/?token=${encodeURIComponent(token)}` : "/";
    };
  }

  function renderChips(d) {
    const hb = d.heartbeat || {};
    const quoteAge = hb.last_quote_ts ? (Date.now() - Date.parse(hb.last_quote_ts)) / 1000 : null;
    const fresh = quoteAge != null && quoteAge < 120;
    const chips = [
      `<span class="chip ${d.mode === "live" ? "live" : "shadow"}"><span class="pulse"></span>${(d.mode || "unknown").toUpperCase()}</span>`,
      `<span class="chip ${d.kill ? "bad" : "ok"}">${d.kill ? "KILL ON" : "KILL OFF"}</span>`,
      `<span class="chip ${fresh ? "ok" : "warn"}">${fresh ? "Quoting" : "Quiet"} · ${ago(hb.last_quote_ts)}</span>`,
      `<span class="chip">Fills 24h · ${d.fills_live_count || 0} live / ${d.fills_sim_count || 0} sim</span>`,
    ];
    if (!d.supabase) chips.push(`<span class="chip bad">No Supabase</span>`);
    $("status-chips").innerHTML = chips.join("");
  }

  function renderHero(d) {
    const t = d.today || {};
    const net = t.net != null ? Number(t.net) : null;
    const est = t.est_gross != null ? Number(t.est_gross) : null;
    $("hero-value").textContent = net != null ? fmtUsd(net) : fmtUsd(0);
    let sub = "Today net vs ledger";
    if (est != null) {
      const cls = (net || 0) >= 0 ? "up" : "down";
      sub = `<span class="${cls}">${fmtUsd(net || 0)}</span> net · est gross ${fmtUsd(est)}`;
    } else if (!d.today) {
      sub = "No daily PnL row yet for UTC today";
    }
    $("hero-sub").innerHTML = sub;
  }

  function renderKill(d) {
    const btn = $("btn-kill");
    btn.disabled = false;
    if (d.kill) {
      btn.textContent = "Resume";
      btn.className = "pill-btn clear";
    } else {
      btn.textContent = "Kill";
      btn.className = "pill-btn armed";
    }
    const note = (d.kill_meta && d.kill_meta.note) || "Supabase clob_control";
    const ts = d.kill_meta && d.kill_meta.updated_at;
    $("kill-note").textContent = ts ? `${note} · ${ago(ts)}` : note;
  }

  function renderMarkets(d) {
    const list = $("markets-list");
    const markets = d.markets || [];
    $("markets-meta").textContent = `${markets.length} recently quoted`;
    if (!markets.length) {
      list.innerHTML = `<div class="empty">No quotes in the last 6h. If EC2 live is up, check Supabase writes.</div>`;
      return;
    }
    list.innerHTML = markets.map((m) => {
      const mid = m.mid != null ? `${(Number(m.mid) * 100).toFixed(1)}¢` : "—";
      const sides = (m.sides || []).join("/");
      const href = m.slug ? `https://polymarket.com/event/${encodeURIComponent(m.slug)}` : "#";
      return `
        <a class="row" href="${href}" target="_blank" rel="noopener">
          <div>
            <div class="row-title">${shortSlug(m.slug)}</div>
            <div class="row-meta">${sides || "—"} · ${m.shadow ? "shadow" : (m.mode || "")} · ${ago(m.last_ts)}</div>
          </div>
          <div class="row-right">
            <div class="price yes">${mid}</div>
            <div class="badge buy">${m.last_size != null ? Number(m.last_size).toFixed(1) : "—"} sh</div>
          </div>
        </a>`;
    }).join("");
  }

  function renderFills(d) {
    const fills = d.fills_24h || [];
    $("fills-meta").textContent = `${fills.length} rows`;
    const list = $("fills-list");
    if (!fills.length) {
      list.innerHTML = `<div class="empty">No fills in 24h — rewards can still accrue on resting quotes.</div>`;
      return;
    }
    list.innerHTML = fills.map((f) => {
      const side = (f.side || "").toUpperCase();
      const sim = !!f.simulated;
      return `
        <div class="row">
          <div>
            <div class="row-title">${(f.token_id || "").slice(0, 18)}…</div>
            <div class="row-meta">${ago(f.ts)}${sim ? " · simulated" : " · live"}</div>
          </div>
          <div class="row-right">
            <div class="price ${side === "BUY" ? "yes" : "no"}">${fmtPct(f.price)}</div>
            <span class="badge ${side === "BUY" ? "buy" : "sell"}">${side || "?"} ${f.size != null ? Number(f.size).toFixed(1) : ""}</span>
            ${sim ? `<span class="badge sim">sim</span>` : ""}
          </div>
        </div>`;
    }).join("");
  }

  function renderPnl(d) {
    const rows = d.pnl_history || [];
    const list = $("pnl-list");
    if (!rows.length) {
      list.innerHTML = `<div class="empty">No clob_daily_pnl rows yet.</div>`;
      return;
    }
    list.innerHTML = rows.map((r) => {
      const net = Number(r.net || 0);
      const cls = net >= 0 ? "yes" : "no";
      const ratio = r.net_vs_gross != null
        ? `${(Number(r.net_vs_gross) * 100).toFixed(0)}% of est`
        : (r.est_gross ? `${fmtUsd(r.net)} / ${fmtUsd(r.est_gross)}` : "");
      return `
        <div class="row">
          <div>
            <div class="row-title">${r.day}</div>
            <div class="row-meta">${r.note || "—"} · ${ratio}</div>
          </div>
          <div class="row-right">
            <div class="price ${cls}">${fmtUsd(net)}</div>
            <div class="row-meta">rew ${fmtUsd(r.rewards_usd)}</div>
          </div>
        </div>`;
    }).join("");
  }

  async function refresh() {
    try {
      const d = await api("/api/status");
      renderHero(d);
      renderChips(d);
      renderKill(d);
      renderMarkets(d);
      renderFills(d);
      renderPnl(d);
      $("updated").textContent = `Updated ${ago(d.ts)}`;
      window.__last = d;
    } catch (e) {
      if (String(e.message) !== "unauthorized") {
        $("hero-sub").textContent = `Load error: ${e.message || e}`;
      }
    }
  }

  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
      tab.classList.add("active");
      $(`panel-${tab.dataset.tab}`).classList.add("active");
    });
  });

  $("btn-refresh").onclick = () => refresh();
  $("btn-kill").onclick = async () => {
    const d = window.__last;
    if (!d) return;
    const next = !d.kill;
    const label = next ? "KILL quoting?" : "Resume quoting?";
    if (!confirm(label)) return;
    $("btn-kill").disabled = true;
    try {
      await api("/api/kill", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kill: next }),
      });
      await refresh();
    } catch (e) {
      alert(`Kill update failed: ${e.message || e}`);
      $("btn-kill").disabled = false;
    }
  };

  if (token) localStorage.setItem("clob_dash_token", token);
  refresh();
  setInterval(refresh, 20000);
})();
