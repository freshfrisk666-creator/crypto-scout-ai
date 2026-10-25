
(() => {
  const $ = (id) => document.getElementById(id);

  const fmtMoney = (value, maximumFractionDigits = 2) => {
    if (
      value === null ||
      value === undefined ||
      !Number.isFinite(Number(value))
    ) {
      return "—";
    }

    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      maximumFractionDigits,
      minimumFractionDigits: 2
    }).format(Number(value));
  };

  const fmtPrice = (value) => {
    if (
      value === null ||
      value === undefined ||
      !Number.isFinite(Number(value))
    ) {
      return "—";
    }

    const number = Number(value);
    const digits =
      number >= 1000 ? 2 :
      number >= 1 ? 4 :
      number >= 0.01 ? 5 : 9;

    return new Intl.NumberFormat("en-US", {
      maximumFractionDigits: digits
    }).format(number);
  };

  const fmtPct = (value) => {
    if (value === null || value === undefined) {
      return "—";
    }

    const number = Number(value);

    if (!Number.isFinite(number)) {
      return "—";
    }

    return `${number >= 0 ? "+" : ""}${number.toFixed(2)}%`;
  };

  const fmtDate = (value) => {
    if (!value) {
      return "Never";
    }

    const date = new Date(value);

    if (Number.isNaN(date.getTime())) {
      return String(value);
    }

    return date.toLocaleString("en-US", {
      dateStyle: "medium",
      timeStyle: "short",
      timeZone: "Europe/Rome"
    });
  };

  const formatStatus = (value) => {
    if (!value) {
      return "Not available";
    }

    const normalized = String(value)
      .replace(/[_-]+/g, " ")
      .trim();

    const lower = normalized.toLowerCase();

    const knownStatuses = {
      "never run": "Never run",
      "success": "Success",
      "succeeded": "Success",
      "failed": "Failed",
      "failure": "Failed",
      "error": "Error",
      "partial": "Partial"
    };

    if (knownStatuses[lower]) {
      return knownStatuses[lower];
    }

    return normalized.replace(
      /\b[a-z]/g,
      (letter) => letter.toUpperCase()
    );
  };

  const formatReason = (value) => {
    if (!value) {
      return "—";
    }

    const normalized = String(value)
      .replace(/[_-]+/g, " ")
      .trim();

    return normalized.replace(
      /\b[a-z]/g,
      (letter) => letter.toUpperCase()
    );
  };

  const safe = (value) =>
    String(value ?? "—").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#039;"
    })[char]);

  const pnlClass = (value) =>
    Number(value) > 0
      ? "positive"
      : Number(value) < 0
        ? "negative"
        : "";

  const plural = (count, singular, pluralForm = `${singular}s`) =>
    `${count} ${count === 1 ? singular : pluralForm}`;

  const setText = (id, value) => {
    const node = $(id);

    if (node) {
      node.textContent = value;
    }
  };

  function renderOpenPositions(positions) {
    const body = $("open-positions-body");

    if (!positions.length) {
      body.innerHTML = `
        <tr>
          <td colspan="9" class="empty-cell">
            No open paper positions. The system is waiting for a signal
            that passes all configured filters.
          </td>
        </tr>
      `;
      return;
    }

    body.innerHTML = positions.map((position) => {
      const pnl = position.unrealized_pnl_net_estimate;

      const age =
        position.age_hours === null ||
        position.age_hours === undefined
          ? "—"
          : `${Number(position.age_hours).toFixed(1)} h`;

      const current =
        position.current_price === null ||
        position.current_price === undefined
          ? "Price unavailable"
          : fmtPrice(position.current_price);

      const risk =
        position.risk_amount === null ||
        position.risk_amount === undefined
          ? "—"
          : fmtMoney(position.risk_amount);

      const pnlDisplay =
        pnl === null || pnl === undefined
          ? "N/A"
          : fmtMoney(pnl);

      return `
        <tr>
          <td>
            <span class="asset-name">${safe(position.ticker)}</span>
            <span class="asset-symbol">${safe(position.symbol)}</span>
          </td>
          <td>${fmtPrice(position.entry_price)}</td>
          <td>${safe(current)}</td>
          <td>${fmtPrice(position.stop_price)}</td>
          <td>${fmtPrice(position.target_price)}</td>
          <td>${fmtMoney(position.notional)}</td>
          <td>${risk}</td>
          <td class="${pnlClass(pnl)}">${pnlDisplay}</td>
          <td>${age} / ${safe(position.max_holding_hours)} h</td>
        </tr>
      `;
    }).join("");
  }

  function renderSignals(signals) {
    const body = $("signals-body");

    if (!signals.length) {
      body.innerHTML = `
        <tr>
          <td colspan="5" class="empty-cell">
            No eligible signals in the latest scan.
          </td>
        </tr>
      `;
      return;
    }

    body.innerHTML = signals.map((signal) => {
      const score = Number(signal.technical_score);
      const scoreDisplay = Number.isFinite(score)
        ? score.toFixed(2)
        : "—";

      return `
        <tr>
          <td>
            <span class="asset-name">${safe(signal.ticker)}</span>
            <span class="asset-symbol">${safe(signal.symbol)}</span>
          </td>
          <td>${fmtPrice(signal.price)}</td>
          <td>${fmtPrice(signal.stop_price)}</td>
          <td>${fmtPrice(signal.target_price)}</td>
          <td>${scoreDisplay}</td>
        </tr>
      `;
    }).join("");
  }

  function renderClosed(trades) {
    const body = $("closed-positions-body");

    if (!trades.length) {
      body.innerHTML = `
        <tr>
          <td colspan="7" class="empty-cell">
            No closed trades yet.
          </td>
        </tr>
      `;
      return;
    }

    body.innerHTML = trades.map((trade) => `
      <tr>
        <td>
          <span class="asset-name">${safe(trade.ticker)}</span>
          <span class="asset-symbol">${safe(trade.symbol)}</span>
        </td>
        <td>${fmtDate(trade.exit_time)}</td>
        <td>${fmtPrice(trade.exit_price)}</td>
        <td>${safe(formatReason(trade.exit_reason))}</td>
        <td class="${pnlClass(trade.realized_pnl)}">
          ${fmtMoney(trade.realized_pnl)}
        </td>
        <td class="${pnlClass(trade.realized_return_pct)}">
          ${fmtPct(trade.realized_return_pct)}
        </td>
        <td>
          ${
            trade.total_cost === null ||
            trade.total_cost === undefined
              ? "—"
              : fmtMoney(trade.total_cost, 4)
          }
        </td>
      </tr>
    `).join("");
  }

  function render(data) {
    const account = data.account || {};
    const performance = data.performance || {};
    const config = data.configuration || {};
    const positions = data.open_positions || [];
    const signals = data.latest_eligible_signals || [];
    const closed = data.recent_closed_positions || [];

    setText(
      "updated-at",
      `Updated: ${fmtDate(data.updated_at)}`
    );

    setText(
      "scan-status",
      `Last scan: ${fmtDate(data.last_scan_at)} · Status: ${formatStatus(data.last_scan_status)}`
    );

    setText("equity", fmtMoney(account.equity_estimate));
    setText("cash", fmtMoney(account.available_cash));

    setText(
      "realized-pnl",
      fmtMoney(account.realized_pnl_net)
    );

    setText(
      "unrealized-pnl",
      account.unrealized_pnl_net_estimate === null ||
      account.unrealized_pnl_net_estimate === undefined
        ? "N/A"
        : fmtMoney(account.unrealized_pnl_net_estimate)
    );

    setText(
      "total-return",
      `Estimated return: ${fmtPct(account.total_return_pct_estimate)}`
    );

    const openCount = Number(
      account.open_positions ?? positions.length
    );

    const maxPositions = Number(
      account.max_positions ?? 3
    );

    const closedCount = Number(
      performance.trades_closed || 0
    );

    const winners = Number(
      performance.winners || 0
    );

    const losers = Number(
      performance.losers || 0
    );

    setText("open-count", String(openCount));

    setText(
      "position-limit",
      `Limit: ${plural(maxPositions, "position")}`
    );

    setText(
      "win-rate",
      `${Number(performance.win_rate_pct || 0).toFixed(1)}%`
    );

    setText(
      "trade-count",
      `${plural(closedCount, "closed trade")} · ${winners} winners / ${losers} losers`
    );

    setText(
      "open-badge",
      plural(positions.length, "position")
    );

    setText(
      "closed-badge",
      plural(closedCount, "trade")
    );

    setText(
      "regime-tf",
      config.regime_timeframe || "4h"
    );

    setText(
      "signal-tf",
      config.signal_timeframe || "1h"
    );

    setText(
      "monitor-tf",
      config.monitor_timeframe || "5m"
    );

    setText(
      "max-holding",
      `${config.max_holding_hours ?? 48} hours`
    );

    setText(
      "risk-pct",
      `${config.risk_per_trade_pct ?? 0.25}% of starting capital`
    );

    setText(
      "notional-pct",
      `${(
        Number(config.max_position_notional_pct ?? 0.1) * 100
      ).toFixed(1)}% of starting capital`
    );

    setText(
      "initial-cash",
      fmtMoney(account.starting_cash)
    );

    const safeMode =
      config.live_orders_enabled === false &&
      config.leverage_enabled === false &&
      data.mode === "PAPER";

    const safety = $("safety-status");

    safety.textContent = safeMode
      ? "Real orders OFF · Leverage OFF"
      : "WARNING: Check safety configuration";

    safety.classList.toggle("unsafe", !safeMode);

    setText(
      "footer-status",
      `Version ${data.version || "—"} · Data published by GitHub Actions.`
    );

    renderOpenPositions(positions);
    renderSignals(signals);
    renderClosed(closed);
  }

  async function refresh() {
    const button = $("refresh-button");

    button.disabled = true;
    button.textContent = "Refreshing...";

    try {
      const response = await fetch(
        `./data.json?cache=${Date.now()}`,
        { cache: "no-store" }
      );

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const data = await response.json();
      render(data);
    } catch (error) {
      setText("updated-at", "Data unavailable");

      $("open-positions-body").innerHTML = `
        <tr>
          <td colspan="9" class="empty-cell">
            Unable to load dashboard/data.json
            (${safe(error.message)}).
            Check whether GitHub Actions completed the deployment.
          </td>
        </tr>
      `;

      $("signals-body").innerHTML = `
        <tr>
          <td colspan="5" class="empty-cell">
            Signal data unavailable.
          </td>
        </tr>
      `;

      $("closed-positions-body").innerHTML = `
        <tr>
          <td colspan="7" class="empty-cell">
            Trade history unavailable.
          </td>
        </tr>
      `;

      setText(
        "footer-status",
        "Error loading dashboard data. Check the GitHub Actions workflow."
      );
    } finally {
      button.disabled = false;
      button.textContent = "Refresh";
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("refresh-button").addEventListener("click", refresh);
    refresh();
  });
})();
