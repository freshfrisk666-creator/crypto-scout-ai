(() => {
  const $ = (id) => document.getElementById(id);
  const fmtMoney = (value, maximumFractionDigits = 2) => {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
    return new Intl.NumberFormat("it-IT", {
      style: "currency", currency: "USD", maximumFractionDigits,
      minimumFractionDigits: 2
    }).format(Number(value));
  };
  const fmtPrice = (value) => {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
    const n = Number(value);
    const digits = n >= 1000 ? 2 : n >= 1 ? 4 : n >= 0.01 ? 5 : 9;
    return new Intl.NumberFormat("it-IT", { maximumFractionDigits: digits }).format(n);
  };
  const fmtPct = (value) => {
    const n = Number(value);
    return Number.isFinite(n) ? `${n >= 0 ? "+" : ""}${n.toFixed(2)}%` : "—";
  };
  const fmtDate = (value) => {
    if (!value) return "Mai";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("it-IT", { dateStyle: "short", timeStyle: "short", timeZone: "Europe/Rome" });
  };
  const safe = (value) => String(value ?? "—").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
  })[char]);
  const pnlClass = (value) => Number(value) > 0 ? "positive" : Number(value) < 0 ? "negative" : "";
  const setText = (id, value) => { const node = $(id); if (node) node.textContent = value; };

  function renderOpenPositions(positions) {
    const body = $("open-positions-body");
    if (!positions.length) {
      body.innerHTML = '<tr><td colspan="9" class="empty-cell">Nessuna posizione virtuale aperta. Il sistema aspetta un segnale che superi tutti i filtri.</td></tr>';
      return;
    }
    body.innerHTML = positions.map((p) => {
      const pnl = p.unrealized_pnl_net_estimate;
      const age = p.age_hours === null || p.age_hours === undefined ? "—" : `${Number(p.age_hours).toFixed(1)} h`;
      const current = p.current_price === null || p.current_price === undefined ? "Prezzo n/d" : fmtPrice(p.current_price);
      return `<tr>
        <td><span class="asset-name">${safe(p.ticker)}</span><span class="asset-symbol">${safe(p.symbol)}</span></td>
        <td>${fmtPrice(p.entry_price)}</td>
        <td>${current}</td>
        <td>${fmtPrice(p.stop_price)}</td>
        <td>${fmtPrice(p.target_price)}</td>
        <td>${fmtMoney(p.notional)}</td>
        <td>${p.risk_amount === null || p.risk_amount === undefined ? "—" : fmtMoney(p.risk_amount)}</td>
        <td class="${pnlClass(pnl)}">${pnl === null || pnl === undefined ? "n/d" : fmtMoney(pnl)}</td>
        <td>${age} / ${safe(p.max_holding_hours)} h</td>
      </tr>`;
    }).join("");
  }

  function renderSignals(signals) {
    const body = $("signals-body");
    if (!signals.length) {
      body.innerHTML = '<tr><td colspan="5" class="empty-cell">Nessun segnale eleggibile nell’ultima scansione.</td></tr>';
      return;
    }
    body.innerHTML = signals.map((s) => `<tr>
      <td><span class="asset-name">${safe(s.ticker)}</span><span class="asset-symbol">${safe(s.symbol)}</span></td>
      <td>${fmtPrice(s.price)}</td><td>${fmtPrice(s.stop_price)}</td><td>${fmtPrice(s.target_price)}</td><td>${Number(s.technical_score).toFixed(2)}</td>
    </tr>`).join("");
  }

  function renderClosed(trades) {
    const body = $("closed-positions-body");
    if (!trades.length) {
      body.innerHTML = '<tr><td colspan="7" class="empty-cell">Nessun trade chiuso finora.</td></tr>';
      return;
    }
    body.innerHTML = trades.map((p) => `<tr>
      <td><span class="asset-name">${safe(p.ticker)}</span><span class="asset-symbol">${safe(p.symbol)}</span></td>
      <td>${fmtDate(p.exit_time)}</td>
      <td>${fmtPrice(p.exit_price)}</td>
      <td>${safe(p.exit_reason || "—")}</td>
      <td class="${pnlClass(p.realized_pnl)}">${fmtMoney(p.realized_pnl)}</td>
      <td class="${pnlClass(p.realized_return_pct)}">${fmtPct(p.realized_return_pct)}</td>
      <td>${p.total_cost === null || p.total_cost === undefined ? "—" : fmtMoney(p.total_cost, 4)}</td>
    </tr>`).join("");
  }

  function render(data) {
    const account = data.account || {};
    const performance = data.performance || {};
    const config = data.configuration || {};
    const positions = data.open_positions || [];
    const signals = data.latest_eligible_signals || [];
    const closed = data.recent_closed_positions || [];

    setText("updated-at", `Aggiornata: ${fmtDate(data.updated_at)}`);
    setText("scan-status", `Ultima scansione: ${fmtDate(data.last_scan_at)} · ${data.last_scan_status || "stato n/d"}`);
    setText("equity", fmtMoney(account.equity_estimate));
    setText("cash", fmtMoney(account.available_cash));
    setText("realized-pnl", fmtMoney(account.realized_pnl_net));
    setText("unrealized-pnl", account.unrealized_pnl_net_estimate === null ? "n/d" : fmtMoney(account.unrealized_pnl_net_estimate));
    setText("total-return", `Rendimento stimato: ${fmtPct(account.total_return_pct_estimate)}`);
    setText("open-count", String(account.open_positions ?? positions.length));
    setText("position-limit", `Limite: ${account.max_positions ?? 3} posizioni`);
    setText("win-rate", `${Number(performance.win_rate_pct || 0).toFixed(1)}%`);
    setText("trade-count", `${performance.trades_closed || 0} trade chiusi · ${performance.winners || 0} vincenti / ${performance.losers || 0} perdenti`);
    setText("open-badge", `${positions.length} posizioni`);
    setText("closed-badge", `${performance.trades_closed || 0} trade`);
    setText("regime-tf", config.regime_timeframe || "4h");
    setText("signal-tf", config.signal_timeframe || "1h");
    setText("monitor-tf", config.monitor_timeframe || "5m");
    setText("max-holding", `${config.max_holding_hours ?? 48} ore`);
    setText("risk-pct", `${config.risk_per_trade_pct ?? 0.25}% del capitale iniziale`);
    setText("notional-pct", `${(Number(config.max_position_notional_pct ?? 0.1) * 100).toFixed(1)}% del capitale iniziale`);
    setText("initial-cash", fmtMoney(account.starting_cash));

    const safeMode = config.live_orders_enabled === false && config.leverage_enabled === false && data.mode === "PAPER";
    const safety = $("safety-status");
    safety.textContent = safeMode ? "Ordini reali OFF · Leva OFF" : "ATTENZIONE: verificare la configurazione";
    safety.classList.toggle("unsafe", !safeMode);
    setText("footer-status", `Versione ${data.version || "—"} · Dati pubblicati da GitHub Actions.`);

    renderOpenPositions(positions);
    renderSignals(signals);
    renderClosed(closed);
  }

  async function refresh() {
    const button = $("refresh-button");
    button.disabled = true;
    button.textContent = "Aggiornamento…";
    try {
      const response = await fetch(`./data.json?cache=${Date.now()}`, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      render(await response.json());
    } catch (error) {
      setText("updated-at", "Dati non disponibili");
      $("open-positions-body").innerHTML = `<tr><td colspan="9" class="empty-cell">Impossibile caricare dashboard/data.json (${safe(error.message)}). Controlla che GitHub Actions abbia completato il deploy.</td></tr>`;
      $("signals-body").innerHTML = '<tr><td colspan="5" class="empty-cell">Dati non disponibili.</td></tr>';
      setText("footer-status", "Errore nel caricamento dei dati.");
    } finally {
      button.disabled = false;
      button.textContent = "Aggiorna";
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("refresh-button").addEventListener("click", refresh);
    refresh();
  });
})();
