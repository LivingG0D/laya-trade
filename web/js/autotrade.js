// ========================================================
// AUTONOMOUS TRADING ENGINE CONTROLLER & UI BINDINGS
// Manages auto-trading state, configuration, safety circuit
// breakers, emergency kill switches, and live audit telemetry.
// ========================================================

import { showToast, playSignalChime } from "./feedback.js";
import { formatPrice, formatUsd } from "./format.js";

let deps = {
  getSymbol: () => "BTCUSDT.P",
  fetchToobitStatus: () => {},
  fetchToobitPositions: () => {}
};

// Values shown on several cards at once (simple card, pro card, modal) carry data-at="<key>"
const atAll = (key) => document.querySelectorAll(`[data-at="${key}"]`);

// DOM Elements — Top Nav
const btnAutoTradeModal = document.getElementById("btnAutoTradeModal");
const navAutoTradeDot = document.getElementById("navAutoTradeDot");
const navAutoTradePill = document.getElementById("navAutoTradePill");

// DOM Elements — Simple Mode Card
const simpleAtStatusDot = document.getElementById("simpleAtStatusDot");
const simpleAtStatusText = document.getElementById("simpleAtStatusText");
const simpleAtRiskVal = document.getElementById("simpleAtRiskVal");

// DOM Elements — Pro Mode Card
const proAtModeBadge = document.getElementById("proAtModeBadge");
const proAtStatus = document.getElementById("proAtStatus");
const proAtSizing = document.getElementById("proAtSizing");
const proAtCircuit = document.getElementById("proAtCircuit");
const proAtLogSnippet = document.getElementById("proAtLogSnippet");

// DOM Elements — Full Modal
const autoTradeModal = document.getElementById("autoTradeModal");
const btnCloseAutoTradeModal = document.getElementById("btnCloseAutoTradeModal");
const modalAtStatusPill = document.getElementById("modalAtStatusPill");
const modalAtStatusText = document.getElementById("modalAtStatusText");
const modalAtModeTag = document.getElementById("modalAtModeTag");
const modalAtPositionsVal = document.getElementById("modalAtPositionsVal");
const atDeskSubtitle = document.getElementById("atDeskSubtitle");

// Execution State Banner
const atRiskBanner = document.getElementById("atRiskBanner");
const atRiskTitle = document.getElementById("atRiskTitle");
const atRiskDetail = document.getElementById("atRiskDetail");
const atRiskTag = document.getElementById("atRiskTag");

// Live Desk
const atDeskPositions = document.getElementById("atDeskPositions");
const atDeskPositionCount = document.getElementById("atDeskPositionCount");
const atDeskSignals = document.getElementById("atDeskSignals");
const atDeskScanNote = document.getElementById("atDeskScanNote");

// Modal Tabs
const tabAtDesk = document.getElementById("tabAtDesk");
const tabAtSettings = document.getElementById("tabAtSettings");
const tabAtSafety = document.getElementById("tabAtSafety");
const tabAtLogs = document.getElementById("tabAtLogs");
const tabContentAtDesk = document.getElementById("tabContentAtDesk");
const tabContentAtSettings = document.getElementById("tabContentAtSettings");
const tabContentAtSafety = document.getElementById("tabContentAtSafety");
const tabContentAtLogs = document.getElementById("tabContentAtLogs");

// Form Inputs — Strategy Settings
const atSettingsForm = document.getElementById("atSettingsForm");
const atSettingMode = document.getElementById("atSettingMode");
const atSettingLeverage = document.getElementById("atSettingLeverage");
const atSettingSizingMode = document.getElementById("atSettingSizingMode");
const atSettingMarginVal = document.getElementById("atSettingMarginVal");
const atMarginUnit = document.getElementById("atMarginUnit");
const atMarginPresetChips = document.getElementById("atMarginPresetChips");
const atSettingMinConf = document.getElementById("atSettingMinConf");
const atSettingCooldown = document.getElementById("atSettingCooldown");
const atSettingAutoBracket = document.getElementById("atSettingAutoBracket");
const atSettingAutoBreakeven = document.getElementById("atSettingAutoBreakeven");
const atSettingReversalExit = document.getElementById("atSettingReversalExit");
const atSymbolChips = document.getElementById("atSymbolChips");
const atTimeframeChips = document.getElementById("atTimeframeChips");
const atSettingMaxPositions = document.getElementById("atSettingMaxPositions");
const atSettingShadowMode = document.getElementById("atSettingShadowMode");
const atSettingPerSymbolCooldown = document.getElementById("atSettingPerSymbolCooldown");
const atShadowToggleRow = document.querySelector(".at-shadow-toggle");

// Form Inputs — Safety Circuit Breakers
const atSafetyForm = document.getElementById("atSafetyForm");
const atSettingMaxDailyLoss = document.getElementById("atSettingMaxDailyLoss");
const atSettingMaxConsec = document.getElementById("atSettingMaxConsec");
const safetyDailyLimitLabel = document.getElementById("safetyDailyLimitLabel");
const safetyConsecLosses = document.getElementById("safetyConsecLosses");
const atLiquidationVal = document.getElementById("atLiquidationVal");
const atLiquidationSub = document.getElementById("atLiquidationSub");

// Audit Logs
const atTerminalBox = document.getElementById("atTerminalBox");
const btnClearAtLogs = document.getElementById("btnClearAtLogs");

// Local state cache
let autoTradeState = {
  enabled: false,
  mode: "demo",
  status: "IDLE",
  status_message: "Auto-trading standby",
  settings: {
    mode: "demo",
    leverage: 10,
    sizing_mode: "fixed_margin",
    fixed_margin_usd: 1.0,
    equity_risk_pct: 1.0,
    min_confidence: 70.0,
    symbols: ["BTCUSDT.P", "ETHUSDT.P", "SOLUSDT.P"],
    timeframes: ["5m", "15m", "1h"],
    max_positions: 2,
    per_symbol_cooldown_seconds: 60,
    signal_ttl_seconds: 15,
    shadow_mode: true,
    auto_bracket: true,
    auto_breakeven: true,
    opposing_reversal_exit: true,
    max_daily_loss_usd: 5.0,
    max_consecutive_losses: 3,
    cooldown_seconds: 30
  },
  stats: {
    total_trades: 0,
    winning_trades: 0,
    losing_trades: 0,
    total_realized_pnl: 0.0,
    daily_realized_pnl: 0.0,
    consecutive_losses: 0,
    win_rate: 0.0,
    cooldown_remaining: 0
  },
  active_trades: [],
  analysis: {},
  logs: []
};

let pollInterval = null;

function openAutoTradeModal() {
  if (!autoTradeModal) return;
  if (!autoTradeModal.open) autoTradeModal.showModal();
  renderAutoTradeState();
  refreshDeskExchangePositions();
  if (typeof lucide !== "undefined" && lucide.createIcons) {
    lucide.createIcons();
  }
}

function closeAutoTradeModal() {
  autoTradeModal?.close();
}

function switchModalTab(tabName) {
  const tabs = [
    { btn: tabAtDesk, content: tabContentAtDesk, id: "desk" },
    { btn: tabAtSettings, content: tabContentAtSettings, id: "settings" },
    { btn: tabAtSafety, content: tabContentAtSafety, id: "safety" },
    { btn: tabAtLogs, content: tabContentAtLogs, id: "logs" }
  ];

  tabs.forEach((t) => {
    if (!t.btn || !t.content) return;
    if (t.id === tabName) {
      t.btn.classList.add("active");
      t.btn.setAttribute("aria-selected", "true");
      t.content.classList.remove("hide");
      t.content.classList.add("active");
    } else {
      t.btn.classList.remove("active");
      t.btn.setAttribute("aria-selected", "false");
      t.content.classList.add("hide");
      t.content.classList.remove("active");
    }
  });

  if (typeof lucide !== "undefined" && lucide.createIcons) {
    lucide.createIcons();
  }
}

// ========================================================
// API Interactions
// ========================================================

export async function fetchAutoTradeStatus() {
  try {
    const res = await fetch("/api/autotrade/status");
    if (!res.ok) return;
    const data = await res.json();
    autoTradeState = data;
    lastStatusAt = Date.now();
    renderAutoTradeState();
  } catch (e) {
    console.warn("Could not fetch AutoTrader status:", e);
  }
}

// Pull exchange positions so the desk can show live mark price and unrealized PnL.
async function refreshDeskExchangePositions() {
  try {
    const res = await fetch("/api/toobit/positions");
    if (!res.ok) return;
    const data = await res.json();
    deskExchangePositions = Array.isArray(data.all_positions) ? data.all_positions : [];
    renderDeskPositions();
  } catch (e) {
    console.warn("Could not fetch positions for desk:", e);
  }
}

async function toggleAutoTrade(forceState = null) {
  const targetState = forceState !== null ? forceState : !autoTradeState.enabled;
  const settings = autoTradeState.settings || {};
  const goingLive = targetState && settings.shadow_mode === false;
  const isLiveMode = String(autoTradeState.mode || settings.mode || "demo").toUpperCase() === "LIVE";

  if (goingLive) {
    const openCount = Array.isArray(autoTradeState.active_trades) ? autoTradeState.active_trades.length : 0;
    const approved = confirm(
      `LIVE TRADING\n\n` +
      `Shadow mode is OFF, so this will send real orders to ${isLiveMode ? "Toobit Futures" : "the exchange"}.\n\n` +
      `• ${settings.leverage || "?"}x leverage, ${settings.sizing_mode === "pct_equity" ? `${settings.equity_risk_pct}% equity` : `${formatUsd(settings.fixed_margin_usd ?? 0)} margin`} per position\n` +
      `• Up to ${settings.max_positions || 1} concurrent position(s)\n` +
      `• Entries require ${settings.min_confidence || "?"}% confidence or higher\n` +
      `• ${openCount} position(s) currently open\n\n` +
      `Confirm you want real capital at risk.`
    );
    if (!approved) return;
  }

  try {
    const res = await fetch("/api/autotrade/toggle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: targetState })
    });
    const data = await res.json();
    if (data.success) {
      autoTradeState = data.status;
      renderAutoTradeState();
      if (targetState) {
        showToast(
          autoTradeState.settings?.shadow_mode === false
            ? "🟢 Live Auto-Trading ARMED — real orders enabled."
            : "🟢 Auto-Trading ARMED in SHADOW mode (no orders).",
          autoTradeState.settings?.shadow_mode === false ? "bear" : "bull"
        );
        playSignalChime("STRONG_LONG");
      } else {
        showToast("⚪ Auto-Trading Disabled. Standing by.", "neutral");
      }
    } else {
      showToast(data.message || "Failed to toggle AutoTrader", "error");
    }
  } catch (e) {
    showToast(`Error toggling AutoTrader: ${e.message}`, "error");
  }
}

async function saveSettings(e) {
  if (e) e.preventDefault();
  const sizingMode = atSettingSizingMode?.value || "fixed_margin";
  const marginVal = parseFloat(atSettingMarginVal?.value || "1.0");
  const symbols = readChipSelection("symbol");
  const timeframes = readChipSelection("timeframe");

  if (symbols.length === 0) {
    showToast("Select at least one symbol to scan.", "error");
    return;
  }
  if (timeframes.length === 0) {
    showToast("Select at least one timeframe.", "error");
    return;
  }

  const newSettings = {
    symbols,
    timeframes,
    max_positions: Math.max(1, Math.min(2, parseInt(atSettingMaxPositions?.value || "2", 10) || 2)),
    per_symbol_cooldown_seconds: Math.max(0, Math.min(600, parseInt(atSettingPerSymbolCooldown?.value || "60", 10) || 0)),
    shadow_mode: !!(atSettingShadowMode?.checked ?? true),
    mode: atSettingMode?.value || "demo",
    leverage: parseInt(atSettingLeverage?.value || "10", 10),
    sizing_mode: sizingMode,
    fixed_margin_usd: sizingMode === "fixed_margin" ? marginVal : autoTradeState.settings.fixed_margin_usd,
    equity_risk_pct: sizingMode === "pct_equity" ? marginVal : autoTradeState.settings.equity_risk_pct,
    min_confidence: parseFloat(atSettingMinConf?.value || "70.0"),
    cooldown_seconds: parseInt(atSettingCooldown?.value || "30", 10),
    auto_bracket: !!atSettingAutoBracket?.checked,
    auto_breakeven: !!atSettingAutoBreakeven?.checked,
    opposing_reversal_exit: !!atSettingReversalExit?.checked
  };

  try {
    const res = await fetch("/api/autotrade/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ settings: newSettings })
    });
    const data = await res.json();
    if (data.success) {
      autoTradeState = data.status;
      renderAutoTradeState();
      if (!newSettings.shadow_mode) {
        showToast("Settings saved. SHADOW MODE IS OFF — real orders can be sent.", "warn", 7000);
      } else if (autoTradeState.enabled) {
        showToast("Strategy settings saved & active!", "bull");
      } else {
        showToast("Settings saved! Note: Auto-Trader is still OFF — flip Master Switch to turn ON.", "warn", 6000);
      }
    } else {
      showToast("Failed to save settings", "error");
    }
  } catch (e) {
    showToast(`Error saving settings: ${e.message}`, "error");
  }
}

async function saveSafetySettings(e) {
  if (e) e.preventDefault();
  const safetySettings = {
    max_daily_loss_usd: parseFloat(atSettingMaxDailyLoss?.value || "5.0"),
    max_consecutive_losses: parseInt(atSettingMaxConsec?.value || "3", 10)
  };

  try {
    const res = await fetch("/api/autotrade/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ settings: safetySettings })
    });
    const data = await res.json();
    if (data.success) {
      autoTradeState = data.status;
      renderAutoTradeState();
      showToast("Circuit breaker safety limits updated!", "bull");
    }
  } catch (e) {
    showToast(`Error updating circuit breakers: ${e.message}`, "error");
  }
}

async function executeEmergencyStop() {
  const confirmed = confirm("🚨 EMERGENCY KILL SWITCH:\n\nThis will immediately HALT auto-trading, cancel all open orders, and market flash-close all positions.\n\nAre you sure?");
  if (!confirmed) return;

  try {
    const res = await fetch("/api/autotrade/emergency_stop", { method: "POST" });
    const data = await res.json();
    if (data.success) {
      autoTradeState = data.status;
      renderAutoTradeState();
      showToast("🚨 EMERGENCY KILL SWITCH TRIGGERED! Positions closed.", "bear");
      playSignalChime("STRONG_SHORT");
      if (deps.fetchToobitPositions) deps.fetchToobitPositions();
      if (deps.fetchToobitStatus) deps.fetchToobitStatus();
    }
  } catch (e) {
    showToast(`Emergency stop failed: ${e.message}`, "error");
  }
}

async function clearLogs() {
  try {
    const res = await fetch("/api/autotrade/clear_logs", { method: "POST" });
    if (res.ok) {
      autoTradeState.logs = [];
      renderLogs();
      showToast("Audit logs cleared", "neutral");
    }
  } catch (e) {
    console.warn("Could not clear logs:", e);
  }
}

// ========================================================
// State Rendering to UI
// ========================================================

// ========================================================
// Live Desk rendering (positions + multi-timeframe signals)
// ========================================================

// Mirrors src/config.py SUPPORTED_SYMBOLS (the canonical .P pairs)
// and SUPPORTED_TIMEFRAMES so the UI can only offer valid options.
const DESK_SYMBOLS = [
  "BTCUSDT.P", "ETHUSDT.P", "SOLUSDT.P", "BNBUSDT.P", "XRPUSDT.P",
  "1000PEPEUSDT.P", "DOGEUSDT.P", "1000SHIBUSDT.P", "1000BONKUSDT.P",
  "1000FLOKIUSDT.P", "WIFUSDT.P", "POPCATUSDT.P", "NEIROUSDT.P",
  "TURBOUSDT.P", "SUIUSDT.P", "AVAXUSDT.P", "ADAUSDT.P", "NEARUSDT.P",
  "TONUSDT.P", "ARBUSDT.P", "OPUSDT.P", "APTUSDT.P", "SEIUSDT.P",
  "DOTUSDT.P", "LTCUSDT.P", "TAOUSDT.P", "FETUSDT.P", "RENDERUSDT.P",
  "LINKUSDT.P", "TIAUSDT.P", "INJUSDT.P", "KASUSDT.P"
];
const DESK_TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h"];

// Latest exchange positions, merged into the desk for live PnL.
let deskExchangePositions = [];

// When the current analysis snapshot was received. The backend refreshes
// `analysis` in place on every scan, so receipt time bounds its age — the
// candle's own open time is NOT a freshness signal (a 1h candle is always
// up to 60 minutes old by design).
let lastStatusAt = Date.now();

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function shortSymbol(symbol) {
  return String(symbol || "").replace("USDT.P", "").replace("USDT", "");
}

function finiteOrNull(value) {
  const n = Number(value);
  return Number.isFinite(n) && n !== 0 ? n : null;
}

// Reward/risk in R units for the current mark price.
function computeRMultiple(trade, markPrice) {
  const entry = Number(trade?.entry_price);
  const sl = Number(trade?.stop_loss);
  const mark = Number(markPrice);
  if (!Number.isFinite(entry) || !Number.isFinite(sl) || !Number.isFinite(mark)) return null;
  const risk = Math.abs(entry - sl);
  if (risk <= 0) return null;
  const dir = String(trade?.side || "").toUpperCase() === "LONG" ? 1 : -1;
  return ((mark - entry) * dir) / risk;
}

function formatAge(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h${Math.floor((s % 3600) / 60)}m`;
  return `${Math.floor(s / 86400)}d`;
}

function findExchangePosition(symbol) {
  return deskExchangePositions.find(
    (p) => p.displaySymbol === symbol || p.rawSymbol === symbol || p.symbol === symbol
  ) || null;
}

function renderDeskPositions() {
  if (!atDeskPositions) return;
  const trades = Array.isArray(autoTradeState.active_trades) ? autoTradeState.active_trades : [];
  const maxPositions = autoTradeState.settings?.max_positions || 1;

  atDeskPositions.replaceChildren();
  if (atDeskPositionCount) atDeskPositionCount.textContent = String(trades.length);

  if (trades.length === 0) {
    const empty = el("div", "at-empty");
    empty.appendChild(el("i", null, ""));
    empty.querySelector("i")?.setAttribute("data-lucide", "inbox");
    empty.appendChild(el("strong", null, "No open positions"));
    empty.appendChild(el("span", null, `The desk is scanning for entries. It will hold at most ${maxPositions} position${maxPositions === 1 ? "" : "s"} at a time.`));
    atDeskPositions.appendChild(empty);
    return;
  }

  trades.forEach((trade) => {
    const symbol = trade.symbol;
    const pos = findExchangePosition(symbol);
    const side = String(trade.side || "").toUpperCase();
    const isLong = side === "LONG";
    const mark = finiteOrNull(pos?.markPrice);
    const pnl = Number(pos?.unrealizedPnl);
    const rMultiple = computeRMultiple(trade, mark);

    const card = el("div", `at-position-card ${isLong ? "side-long" : "side-short"}`);

    // Header: symbol + side + live PnL
    const head = el("div", "at-pos-head");
    const sym = el("div", "at-pos-symbol");
    sym.appendChild(el("strong", null, shortSymbol(symbol)));
    sym.appendChild(el("span", `at-pos-side ${isLong ? "long" : "short"}`, side));
    head.appendChild(sym);

    const pnlWrap = el("div", `at-pos-pnl ${Number.isFinite(pnl) ? (pnl > 0 ? "up" : pnl < 0 ? "down" : "") : ""}`);
    pnlWrap.appendChild(el("strong", null, Number.isFinite(pnl) ? formatUsd(pnl) : "—"));
    pnlWrap.appendChild(el("span", null, rMultiple !== null ? `${rMultiple >= 0 ? "+" : ""}${rMultiple.toFixed(2)}R` : "R —"));
    head.appendChild(pnlWrap);
    card.appendChild(head);

    // Core numbers
    const grid = el("div", "at-pos-grid");
    const fields = [
      ["Entry", trade.entry_price ? formatPrice(Number(trade.entry_price)) : "—"],
      ["Mark", mark ? formatPrice(mark) : "—"],
      ["Size", `${trade.quantity ?? "—"} @ ${pos?.leverage ?? autoTradeState.settings?.leverage ?? "?"}x`],
      ["Margin", formatUsd(trade.margin)]
    ];
    fields.forEach(([k, v]) => {
      const f = el("div", "at-pos-field");
      f.appendChild(el("span", "k", k));
      f.appendChild(el("span", "v", v));
      grid.appendChild(f);
    });
    card.appendChild(grid);

    // Risk ladder: SL -> entry -> TP1 (and TP2 when present)
    const ladder = el("div", "at-pos-ladder");
    const rungs = [
      { k: "SL", v: trade.stop_loss, cls: "sl" },
      { k: "Entry", v: trade.entry_price, cls: "entry" }
    ];
    if (trade.take_profit_1) rungs.push({ k: "TP1", v: trade.take_profit_1, cls: "tp" });
    if (trade.take_profit_2) rungs.push({ k: "TP2", v: trade.take_profit_2, cls: "tp" });

    rungs.forEach((rung, i) => {
      if (i > 0) ladder.appendChild(el("span", "arrow", "›"));
      const node = el("div", `rung ${rung.cls}`);
      node.appendChild(el("span", "rk", rung.k));
      node.appendChild(el("span", "rv", rung.v ? formatPrice(Number(rung.v)) : "—"));
      ladder.appendChild(node);
    });
    card.appendChild(ladder);

    // Footer: protection state + age
    const foot = el("div", "at-pos-foot");
    const tags = el("div", "at-pos-tags");

    const slConfirmed = !!pos?.stopLoss;
    const tpConfirmed = !!pos?.takeProfit || !!pos?.takeProfit1;
    if (slConfirmed && tpConfirmed) {
      tags.appendChild(el("span", "at-pos-tag ok", "BRACKETED"));
    } else {
      tags.appendChild(el("span", "at-pos-tag warn", slConfirmed ? "TP MISSING" : "UNPROTECTED"));
    }
    if (trade.be_moved) tags.appendChild(el("span", "at-pos-tag be", "BREAK-EVEN"));
    if (autoTradeState.settings?.shadow_mode) tags.appendChild(el("span", "at-pos-tag", "SHADOW"));
    if (!pos) tags.appendChild(el("span", "at-pos-tag warn", "NO EXCHANGE MATCH"));
    foot.appendChild(tags);

    if (trade.open_time) {
      const age = formatAge(Date.now() / 1000 - Number(trade.open_time));
      foot.appendChild(el("span", "at-pos-age", `${age} · TP/SL live`));
    }
    card.appendChild(foot);

    atDeskPositions.appendChild(card);
  });
}

function renderDeskSignals() {
  if (!atDeskSignals) return;
  const analysis = autoTradeState.analysis || {};
  const settings = autoTradeState.settings || {};
  const threshold = Number(settings.min_confidence) || 0;
  const ttl = Number(settings.signal_ttl_seconds) || 30;
  const symbols = Array.isArray(settings.symbols) ? settings.symbols : Object.keys(analysis);

  atDeskSignals.replaceChildren();
  if (!symbols.length) {
    const empty = el("div", "at-empty");
    empty.appendChild(el("strong", null, "No symbols configured"));
    empty.appendChild(el("span", null, "Select at least one symbol in Risk & Strategy Controls."));
    atDeskSignals.appendChild(empty);
    return;
  }

  let readyCount = 0;

  symbols.forEach((symbol) => {
    const data = analysis[symbol] || {};
    const action = String(data.action || "NEUTRAL").toUpperCase();
    const actionKey = action.includes("LONG") ? "long" : action.includes("SHORT") ? "short" : "neutral";
    const confidence = Number(data.confidence_pct);
    const hasConf = Number.isFinite(confidence);

    // Freshness = age of the analysis snapshot, not of the candle.
    const snapshotAge = Math.max(0, (Date.now() - lastStatusAt) / 1000);
    const isStale = snapshotAge > ttl;
    // Age of the source candle: informational only (a 1h candle is always ~1h "old").
    const candleAgeSec = data.last_candle_timestamp
      ? Math.max(0, Date.now() / 1000 - Number(data.last_candle_timestamp))
      : null;
    const state = (data.state || (isStale ? "STALE" : "READY")).toUpperCase();
    const stateKey = state === "ERROR" ? "error" : isStale ? "stale" : "ready";
    if (stateKey === "ready") readyCount += 1;

    const card = el("div", `at-signal-card sig-${actionKey} ${isStale ? "is-stale" : ""}`);

    const head = el("div", "at-sig-head");
    head.appendChild(el("span", "at-sig-symbol", shortSymbol(symbol)));
    head.appendChild(el("span", `at-sig-action ${actionKey}`, action));
    card.appendChild(head);

    const confRow = el("div", "at-sig-conf");
    confRow.appendChild(el("span", null, "Confidence"));
    confRow.appendChild(el("b", null, hasConf ? `${confidence.toFixed(1)}%` : "—"));
    card.appendChild(confRow);

    const bar = el("div", "at-sig-bar");
    const fill = el("i");
    fill.style.width = hasConf ? `${Math.max(0, Math.min(100, confidence))}%` : "0%";
    bar.appendChild(fill);
    if (threshold > 0) {
      const mark = el("span", "threshold");
      mark.style.left = `${Math.max(0, Math.min(100, threshold))}%`;
      mark.title = `Entry threshold ${threshold}%`;
      bar.appendChild(mark);
    }
    card.appendChild(bar);

    const meta = el("div", "at-sig-meta");
    const tfs = el("div", "at-sig-tfs");
    (data.timeframes || settings.timeframes || []).forEach((tf) => tfs.appendChild(el("span", "at-sig-tf", tf)));
    meta.appendChild(tfs);

    const stateWrap = el("div", `at-sig-state ${stateKey}`);
    stateWrap.appendChild(el("span", null, stateKey === "ready" ? "LIVE" : stateKey === "error" ? "ERROR" : "STALE"));
    stateWrap.appendChild(el("span", null, `· ${formatAge(snapshotAge)}`));
    const latency = Number(data.latency_ms);
    stateWrap.title =
      `Analysis snapshot ${formatAge(snapshotAge)} old (TTL ${ttl}s)` +
      (candleAgeSec !== null ? `\nSource candle closed ${formatAge(candleAgeSec)} ago` : "") +
      (Number.isFinite(latency) ? `\nDecision latency ${latency.toFixed(0)}ms` : "");
    meta.appendChild(stateWrap);
    card.appendChild(meta);

    atDeskSignals.appendChild(card);
  });

  if (atDeskScanNote) {
    const scanAge = Math.max(0, (Date.now() - lastStatusAt) / 1000);
    atDeskScanNote.textContent = `${readyCount}/${symbols.length} fresh · ${formatAge(scanAge)} ago · ttl ${ttl}s`;
  }
}

function renderRiskBanner() {
  if (!atRiskBanner) return;
  const settings = autoTradeState.settings || {};
  const isEnabled = !!autoTradeState.enabled;
  const isLive = String(autoTradeState.mode || settings.mode || "demo").toUpperCase() === "LIVE";
  const isShadow = settings.shadow_mode !== false;

  let state = "off";
  if (isEnabled && !isShadow) state = "live";
  else if (isEnabled && isShadow) state = "shadow";

  atRiskBanner.dataset.state = state;

  if (atRiskTag) {
    atRiskTag.textContent = state === "live" ? "REAL CAPITAL" : isShadow ? "PAPER" : "OFF";
  }
  if (atRiskTitle) {
    atRiskTitle.textContent =
      state === "live" ? "LIVE TRADING ACTIVE" : state === "shadow" ? "SHADOW MODE" : "AUTO-TRADER OFF";
  }
  if (atRiskDetail) {
    atRiskDetail.textContent =
      state === "live"
        ? `Real orders are being sent to ${isLive ? "Toobit Futures" : "the exchange"} at up to ${settings.leverage || "?"}x.`
        : state === "shadow"
          ? "Signals are evaluated and logged, but no orders are sent to the exchange."
          : "The supervisor loop is stopped. No analysis or orders are running.";
  }
}

// Liquidation buffer shrinks as leverage rises; derive it instead of hardcoding.
function renderLiquidationEstimate() {
  if (!atLiquidationVal) return;
  const leverage = Number(autoTradeState.settings?.leverage) || 0;
  if (leverage < 1) {
    atLiquidationVal.textContent = "—";
    atLiquidationVal.className = "sm-val";
    if (atLiquidationSub) atLiquidationSub.textContent = "Set leverage to estimate";
    return;
  }
  // ~0.9% maintenance margin is a common perp assumption; state it as an estimate.
  const buffer = (1 / leverage - 0.009) * 100;
  const usable = buffer > 0;
  atLiquidationVal.textContent = usable ? `~${buffer.toFixed(2)}% buffer` : "Very close";
  atLiquidationVal.className = `sm-val ${usable && buffer > 1 ? "pnl-green" : "pnl-red"}`;
  if (atLiquidationSub) {
    atLiquidationSub.textContent = `At ${leverage}x, est. before liquidation. Not a guarantee.`;
  }
}

function renderUniverseChips() {
  if (!atSymbolChips || atSymbolChips.childElementCount > 0) return;
  const selected = new Set(autoTradeState.settings?.symbols || []);
  DESK_SYMBOLS.forEach((symbol) => {
    const chip = el("button", "at-chip", shortSymbol(symbol));
    chip.type = "button";
    chip.dataset.value = symbol;
    chip.dataset.group = "symbol";
    chip.setAttribute("aria-pressed", selected.has(symbol) ? "true" : "false");
    atSymbolChips.appendChild(chip);
  });

  if (atTimeframeChips) {
    const tfSelected = new Set(autoTradeState.settings?.timeframes || []);
    DESK_TIMEFRAMES.forEach((tf) => {
      const chip = el("button", "at-chip", tf);
      chip.type = "button";
      chip.dataset.value = tf;
      chip.dataset.group = "timeframe";
      chip.setAttribute("aria-pressed", tfSelected.has(tf) ? "true" : "false");
      atTimeframeChips.appendChild(chip);
    });
  }
}

function syncUniverseChips() {
  const settings = autoTradeState.settings || {};
  const selectedSymbols = new Set(settings.symbols || []);
  const selectedTfs = new Set(settings.timeframes || []);
  document.querySelectorAll("#atSymbolChips .at-chip").forEach((chip) => {
    chip.setAttribute("aria-pressed", selectedSymbols.has(chip.dataset.value) ? "true" : "false");
  });
  document.querySelectorAll("#atTimeframeChips .at-chip").forEach((chip) => {
    chip.setAttribute("aria-pressed", selectedTfs.has(chip.dataset.value) ? "true" : "false");
  });
}

function readChipSelection(group) {
  return Array.from(document.querySelectorAll(`#at${group === "symbol" ? "Symbol" : "Timeframe"}Chips .at-chip`))
    .filter((chip) => chip.getAttribute("aria-pressed") === "true")
    .map((chip) => chip.dataset.value);
}

export function renderAutoTradeState() {
  const isEnabled = !!autoTradeState.enabled;
  const status = autoTradeState.status || "IDLE";
  const mode = (autoTradeState.settings?.mode || "demo").toUpperCase();
  const settings = autoTradeState.settings || {};
  const stats = autoTradeState.stats || {};
  const dailyPnl = Number(stats.daily_realized_pnl) || 0.0;
  const totalTrades = stats.total_trades || 0;
  const hasClosedTrades = totalTrades > 0;
  const winRate = hasClosedTrades ? Number(stats.win_rate || 0.0).toFixed(1) : "—";
  const winRateLabel = hasClosedTrades ? `${winRate}% (${totalTrades} trades)` : "— (no closed trades)";
  const openPositionCount = Array.isArray(autoTradeState.active_trades)
    ? autoTradeState.active_trades.length
    : 0;
  const maxPositions = settings.max_positions || 1;

  renderRiskBanner();
  renderUniverseChips();
  syncUniverseChips();
  renderDeskPositions();
  renderDeskSignals();
  renderLiquidationEstimate();

  // 1. Top Nav Button Update
  if (navAutoTradeDot) {
    navAutoTradeDot.className = `autotrade-status-dot ${isEnabled ? "active" : ""}`;
  }
  if (navAutoTradePill) {
    navAutoTradePill.textContent = isEnabled ? "ON" : "OFF";
    navAutoTradePill.className = `autotrade-pill ${isEnabled ? "on" : "off"}`;
  }

  // 2. Values repeated across simple card, pro card and modal
  atAll("toggle").forEach((btn) => {
    btn.classList.toggle("active", isEnabled);
    btn.querySelector(".toggle-label").textContent = isEnabled ? "ON" : "OFF";
  });
  atAll("daily-pnl").forEach((node) => {
    node.textContent = formatUsd(dailyPnl, { signed: true });
    node.classList.toggle("pnl-green", dailyPnl >= 0);
    node.classList.toggle("pnl-red", dailyPnl < 0);
  });
  atAll("winrate").forEach((node) => { node.textContent = winRateLabel; });

  // 3. Simple Mode Card Update
  if (simpleAtStatusDot) {
    simpleAtStatusDot.className = `status-pulse-dot ${isEnabled ? (status === "IN_POSITION" ? "busy" : "active") : ""}`;
  }
  if (simpleAtStatusText) {
    simpleAtStatusText.textContent = autoTradeState.status_message || "Standby";
  }
  if (simpleAtRiskVal) {
    const sizingStr = settings.sizing_mode === "pct_equity" ? `${settings.equity_risk_pct}% Eq` : formatUsd(settings.fixed_margin_usd ?? 0);
    simpleAtRiskVal.textContent = `${settings.leverage ?? "?"}x • ${sizingStr} Margin`;
  }

  // 4. Pro Mode Card Update
  if (proAtModeBadge) {
    proAtModeBadge.textContent = mode;
    proAtModeBadge.className = `at-mode-badge ${mode.toLowerCase()}`;
  }
  if (proAtStatus) {
    proAtStatus.textContent = status;
    proAtStatus.className = `val status-${status.toLowerCase()}`;
  }
  if (proAtSizing) {
    const sizingStr = settings.sizing_mode === "pct_equity" ? `${settings.equity_risk_pct}% Eq` : formatUsd(settings.fixed_margin_usd ?? 0);
    proAtSizing.textContent = `${settings.leverage ?? "?"}x • ${sizingStr}`;
  }
  if (proAtCircuit) {
    const maxLoss = settings.max_daily_loss_usd || 5.0;
    const consec = stats.consecutive_losses || 0;
    const maxConsec = settings.max_consecutive_losses || 3;
    proAtCircuit.textContent = `${consec}/${maxConsec} loss (${formatUsd(dailyPnl, { signed: true })} / ${formatUsd(-maxLoss)})`;
  }

  // 5. Modal Status Bar
  if (modalAtStatusPill) {
    modalAtStatusPill.className = `val-pill status-${status.toLowerCase()}`;
  }
  if (modalAtStatusText) {
    modalAtStatusText.textContent = status;
  }
  if (modalAtModeTag) {
    modalAtModeTag.textContent = settings.shadow_mode === false ? `${mode} • REAL` : `${mode} • SHADOW`;
    modalAtModeTag.className = `val-tag ${mode.toLowerCase()}`;
  }
  if (modalAtPositionsVal) {
    modalAtPositionsVal.textContent = `${openPositionCount} / ${maxPositions}`;
    modalAtPositionsVal.className = `val-bold ${openPositionCount >= maxPositions ? "pnl-green" : ""}`;
  }

  // 6. Update Form Field Values if not focused
  if (atSettingMode && document.activeElement !== atSettingMode) {
    atSettingMode.value = settings.mode || "demo";
  }
  if (atSettingLeverage && document.activeElement !== atSettingLeverage) {
    atSettingLeverage.value = String(settings.leverage || 10);
  }
  if (atSettingSizingMode && document.activeElement !== atSettingSizingMode) {
    atSettingSizingMode.value = settings.sizing_mode || "fixed_margin";
  }
  if (atSettingMarginVal && document.activeElement !== atSettingMarginVal) {
    atSettingMarginVal.value = settings.sizing_mode === "pct_equity" ? (settings.equity_risk_pct || 1.0) : (settings.fixed_margin_usd || 1.0);
  }
  if (atMarginUnit) {
    atMarginUnit.textContent = settings.sizing_mode === "pct_equity" ? "% Equity" : "USDT";
  }
  // Keep margin preset chips in sync with the value actually in effect.
  if (atMarginPresetChips && settings.sizing_mode !== "pct_equity") {
    const current = Number(settings.fixed_margin_usd);
    atMarginPresetChips.querySelectorAll(".preset-chip").forEach((chip) => {
      chip.classList.toggle("active", Math.abs(Number(chip.dataset.val) - current) < 0.0001);
    });
  }
  if (atSettingMinConf && document.activeElement !== atSettingMinConf) {
    atSettingMinConf.value = String(settings.min_confidence || 65);
  }
  if (atSettingCooldown && document.activeElement !== atSettingCooldown) {
    atSettingCooldown.value = String(settings.cooldown_seconds || 30);
  }
  if (atSettingAutoBracket) atSettingAutoBracket.checked = !!settings.auto_bracket;
  if (atSettingAutoBreakeven) atSettingAutoBreakeven.checked = !!settings.auto_breakeven;
  if (atSettingReversalExit) atSettingReversalExit.checked = !!settings.opposing_reversal_exit;

  // Universe & execution scope
  if (atSettingMaxPositions && document.activeElement !== atSettingMaxPositions) {
    atSettingMaxPositions.value = String(settings.max_positions || 2);
  }
  if (atSettingPerSymbolCooldown && document.activeElement !== atSettingPerSymbolCooldown) {
    atSettingPerSymbolCooldown.value = String(settings.per_symbol_cooldown_seconds ?? 60);
  }
  if (atSettingShadowMode) atSettingShadowMode.checked = settings.shadow_mode !== false;
  if (atShadowToggleRow) {
    atShadowToggleRow.classList.toggle("is-on", settings.shadow_mode !== false);
    atShadowToggleRow.classList.toggle("is-off", settings.shadow_mode === false);
  }
  if (atDeskSubtitle) {
    const lev = settings.leverage ? `${settings.leverage}x` : "dynamic";
    const count = Array.isArray(settings.symbols) ? settings.symbols.length : 0;
    atDeskSubtitle.textContent = `${count} symbol${count === 1 ? "" : "s"} • ${lev} • max ${settings.max_positions || 1} position${(settings.max_positions || 1) === 1 ? "" : "s"}`;
  }

  if (atSettingMaxDailyLoss && document.activeElement !== atSettingMaxDailyLoss) {
    atSettingMaxDailyLoss.value = String(settings.max_daily_loss_usd || 5.0);
  }
  if (atSettingMaxConsec && document.activeElement !== atSettingMaxConsec) {
    atSettingMaxConsec.value = String(settings.max_consecutive_losses || 3);
  }

  if (safetyDailyLimitLabel) {
    safetyDailyLimitLabel.textContent = `Cap: ${formatUsd(-(settings.max_daily_loss_usd || 5.0))} USDT`;
  }
  if (safetyConsecLosses) {
    safetyConsecLosses.textContent = `${stats.consecutive_losses || 0} / ${settings.max_consecutive_losses || 3}`;
  }

  // 7. Render Logs & Log Snippet
  renderLogs();
}

function renderLogs() {
  const logs = Array.isArray(autoTradeState.logs) ? autoTradeState.logs : [];

  // Pro card snippet: built with textContent so log payloads can never inject markup.
  if (proAtLogSnippet) {
    proAtLogSnippet.replaceChildren();
    const top = logs[0];
    const item = el("div", `snippet-item ${top?.level || "info"}`);
    item.appendChild(el("span", "snippet-time", top?.time || "--:--:--"));
    item.appendChild(el("span", "snippet-cat", `[${top?.category || "SYSTEM"}]`));
    item.appendChild(el("span", "snippet-msg", top?.message || "Auto-trading standing by..."));
    proAtLogSnippet.appendChild(item);
  }

  // Full modal terminal
  if (!atTerminalBox) return;

  if (logs.length === 0) {
    atTerminalBox.replaceChildren();
    const line = el("div", "log-line info");
    line.appendChild(el("span", "log-time", "--:--:--"));
    line.appendChild(el("span", "log-badge system", "SYSTEM"));
    line.appendChild(el("span", "log-msg", "Audit terminal ready. Awaiting events..."));
    atTerminalBox.appendChild(line);
    return;
  }

  const frag = document.createDocumentFragment();
  logs.forEach((l) => {
    const line = el("div", `log-line ${l.level || "info"}`);
    line.appendChild(el("span", "log-time", l.time || ""));
    line.appendChild(el("span", `log-badge ${String(l.category || "system").toLowerCase()}`, l.category || "system"));
    line.appendChild(el("span", "log-msg", l.message || ""));
    frag.appendChild(line);
  });
  atTerminalBox.replaceChildren(frag);
}

// ========================================================
// WebSocket Message Hook
// ========================================================

export function handleAutoTradeWsMessage(data) {
  if (!data) return;
  if (data.type === "autotrade_log" && data.log) {
    if (!autoTradeState.logs) autoTradeState.logs = [];
    autoTradeState.logs.unshift(data.log);
    if (autoTradeState.logs.length > 100) autoTradeState.logs.pop();

    if (data.status) {
      autoTradeState = { ...autoTradeState, ...data.status };
    }
    renderAutoTradeState();

    if (data.log.category === "ORDER" && data.log.level === "success") {
      playSignalChime("STRONG_LONG");
      showToast(`🤖 Auto-Trade Executed: ${data.log.message}`, "bull");
      if (deps.fetchToobitPositions) deps.fetchToobitPositions();
      refreshDeskExchangePositions();
    } else if (data.log.category === "PROFIT_LOCK") {
      showToast(`🛡️ ${data.log.message}`, "bull");
    } else if (data.log.category === "CIRCUIT_BREAKER") {
      showToast(`🚨 ${data.log.message}`, "bear");
    }
  }
}

// ========================================================
// Initialization
// ========================================================

export function initAutoTradeManager(injectedDeps = {}) {
  deps = { ...deps, ...injectedDeps };

  // Modal Open / Close
  btnAutoTradeModal?.addEventListener("click", openAutoTradeModal);
  btnCloseAutoTradeModal?.addEventListener("click", closeAutoTradeModal);
  atAll("config").forEach((btn) => btn.addEventListener("click", () => {
    openAutoTradeModal();
    switchModalTab("settings");
  }));

  autoTradeModal?.addEventListener("click", (e) => {
    if (e.target === autoTradeModal) closeAutoTradeModal();
  });

  // Master Switch Toggles
  atAll("toggle").forEach((btn) => btn.addEventListener("click", () => toggleAutoTrade()));

  // Emergency Stops
  atAll("kill").forEach((btn) => btn.addEventListener("click", executeEmergencyStop));

  // Tabs
  tabAtDesk?.addEventListener("click", () => switchModalTab("desk"));
  tabAtSettings?.addEventListener("click", () => switchModalTab("settings"));
  tabAtSafety?.addEventListener("click", () => switchModalTab("safety"));
  tabAtLogs?.addEventListener("click", () => switchModalTab("logs"));

  // Universe chips (symbols + timeframes)
  const bindChipGroup = (container) => {
    container?.addEventListener("click", (e) => {
      const chip = e.target.closest(".at-chip");
      if (!chip) return;
      const next = chip.getAttribute("aria-pressed") !== "true";
      chip.setAttribute("aria-pressed", next ? "true" : "false");
    });
  };
  bindChipGroup(atSymbolChips);
  bindChipGroup(atTimeframeChips);

  // Shadow mode toggle visual state
  atSettingShadowMode?.addEventListener("change", () => {
    if (!atShadowToggleRow) return;
    atShadowToggleRow.classList.toggle("is-on", atSettingShadowMode.checked);
    atShadowToggleRow.classList.toggle("is-off", !atSettingShadowMode.checked);
  });

  // Forms
  atSettingsForm?.addEventListener("submit", saveSettings);
  atSafetyForm?.addEventListener("submit", saveSafetySettings);
  btnClearAtLogs?.addEventListener("click", clearLogs);

  // Sizing Mode Unit Toggle
  atSettingSizingMode?.addEventListener("change", () => {
    const isPct = atSettingSizingMode.value === "pct_equity";
    if (atMarginUnit) atMarginUnit.textContent = isPct ? "% Equity" : "USDT";
    if (atSettingMarginVal) {
      atSettingMarginVal.value = isPct ? "1.0" : "1.0";
      atSettingMarginVal.step = isPct ? "0.5" : "0.5";
      atSettingMarginVal.max = isPct ? "5.0" : "50.0";
    }
  });

  // Preset Margin Chips
  atMarginPresetChips?.addEventListener("click", (e) => {
    const chip = e.target.closest(".preset-chip");
    if (!chip) return;
    atMarginPresetChips.querySelectorAll(".preset-chip").forEach((c) => c.classList.remove("active"));
    chip.classList.add("active");
    if (atSettingMarginVal) {
      atSettingMarginVal.value = chip.dataset.val;
      if (atSettingSizingMode) atSettingSizingMode.value = "fixed_margin";
      if (atMarginUnit) atMarginUnit.textContent = "USDT";
    }
  });

  // Fetch initial status
  fetchAutoTradeStatus();
  refreshDeskExchangePositions();

  // Periodic status poll (every 6 seconds)
  if (pollInterval) clearInterval(pollInterval);
  pollInterval = setInterval(() => {
    if (document.hidden) return;
    fetchAutoTradeStatus();
    if (autoTradeModal?.open) {
      refreshDeskExchangePositions();
    }
  }, 6000);
}
