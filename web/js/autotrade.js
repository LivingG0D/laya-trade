// ========================================================
// AUTONOMOUS TRADING ENGINE CONTROLLER & UI BINDINGS
// Manages auto-trading state, configuration, safety circuit
// breakers, emergency kill switches, and live audit telemetry.
// ========================================================

import { showToast, playSignalChime } from "./feedback.js";
import { formatPrice } from "./format.js";

let deps = {
  getSymbol: () => "BTCUSDT.P",
  fetchToobitStatus: () => {},
  fetchToobitPositions: () => {}
};

// DOM Elements — Top Nav
const btnAutoTradeModal = document.getElementById("btnAutoTradeModal");
const navAutoTradeDot = document.getElementById("navAutoTradeDot");
const navAutoTradePill = document.getElementById("navAutoTradePill");

// DOM Elements — Simple Mode Card
const btnSimpleAutoTradeToggle = document.getElementById("btnSimpleAutoTradeToggle");
const simpleAutoTradeToggleLabel = document.getElementById("simpleAutoTradeToggleLabel");
const simpleAtStatusDot = document.getElementById("simpleAtStatusDot");
const simpleAtStatusText = document.getElementById("simpleAtStatusText");
const simpleAtRiskVal = document.getElementById("simpleAtRiskVal");
const simpleAtWinRate = document.getElementById("simpleAtWinRate");
const btnSimpleAtConfig = document.getElementById("btnSimpleAtConfig");
const btnSimpleAtKill = document.getElementById("btnSimpleAtKill");

// DOM Elements — Pro Mode Card
const btnProAutoTradeToggle = document.getElementById("btnProAutoTradeToggle");
const proAutoTradeToggleLabel = document.getElementById("proAutoTradeToggleLabel");
const proAtModeBadge = document.getElementById("proAtModeBadge");
const proAtStatus = document.getElementById("proAtStatus");
const proAtSizing = document.getElementById("proAtSizing");
const proAtPnl = document.getElementById("proAtPnl");
const proAtCircuit = document.getElementById("proAtCircuit");
const proAtLogSnippet = document.getElementById("proAtLogSnippet");
const btnProAtConfig = document.getElementById("btnProAtConfig");
const btnProAtKill = document.getElementById("btnProAtKill");

// DOM Elements — Full Modal
const autoTradeModal = document.getElementById("autoTradeModal");
const btnCloseAutoTradeModal = document.getElementById("btnCloseAutoTradeModal");
const modalAutoTradeToggle = document.getElementById("modalAutoTradeToggle");
const modalAutoTradeToggleLabel = document.getElementById("modalAutoTradeToggleLabel");
const modalAtStatusPill = document.getElementById("modalAtStatusPill");
const modalAtStatusDot = document.getElementById("modalAtStatusDot");
const modalAtStatusText = document.getElementById("modalAtStatusText");
const modalAtModeTag = document.getElementById("modalAtModeTag");
const modalAtWinRateVal = document.getElementById("modalAtWinRateVal");
const modalAtDailyPnlVal = document.getElementById("modalAtDailyPnlVal");

// Modal Tabs
const tabAtSettings = document.getElementById("tabAtSettings");
const tabAtSafety = document.getElementById("tabAtSafety");
const tabAtLogs = document.getElementById("tabAtLogs");
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

// Form Inputs — Safety Circuit Breakers
const atSafetyForm = document.getElementById("atSafetyForm");
const atSettingMaxDailyLoss = document.getElementById("atSettingMaxDailyLoss");
const atSettingMaxConsec = document.getElementById("atSettingMaxConsec");
const safetyDailyPnl = document.getElementById("safetyDailyPnl");
const safetyDailyLimitLabel = document.getElementById("safetyDailyLimitLabel");
const safetyConsecLosses = document.getElementById("safetyConsecLosses");
const btnModalEmergencyStop = document.getElementById("btnModalEmergencyStop");

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
    leverage: 50,
    sizing_mode: "fixed_margin",
    fixed_margin_usd: 1.0,
    equity_risk_pct: 1.0,
    min_confidence: 65.0,
    max_positions: 1,
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
  logs: []
};

let pollInterval = null;

export function isAutoTradeModalOpen() {
  return !!(autoTradeModal && !autoTradeModal.classList.contains("hide"));
}

function openAutoTradeModal() {
  if (!autoTradeModal) return;
  autoTradeModal.classList.remove("hide");
  renderAutoTradeState();
  if (typeof lucide !== "undefined" && lucide.createIcons) {
    lucide.createIcons();
  }
}

function closeAutoTradeModal() {
  if (!autoTradeModal) return;
  autoTradeModal.classList.add("hide");
}

function switchModalTab(tabName) {
  const tabs = [
    { btn: tabAtSettings, content: tabContentAtSettings, id: "settings" },
    { btn: tabAtSafety, content: tabContentAtSafety, id: "safety" },
    { btn: tabAtLogs, content: tabContentAtLogs, id: "logs" }
  ];

  tabs.forEach((t) => {
    if (!t.btn || !t.content) return;
    if (t.id === tabName) {
      t.btn.classList.add("active");
      t.btn.setAttribute("aria-selected", "true");
      t.content.classList.add("active");
    } else {
      t.btn.classList.remove("active");
      t.btn.setAttribute("aria-selected", "false");
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
    renderAutoTradeState();
  } catch (e) {
    console.warn("Could not fetch AutoTrader status:", e);
  }
}

async function toggleAutoTrade(forceState = null) {
  const targetState = forceState !== null ? forceState : !autoTradeState.enabled;
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
        showToast("🟢 Auto-Trading ARMED! Laya is monitoring setups.", "bull");
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

  const newSettings = {
    mode: atSettingMode?.value || "demo",
    leverage: parseInt(atSettingLeverage?.value || "50", 10),
    sizing_mode: sizingMode,
    fixed_margin_usd: sizingMode === "fixed_margin" ? marginVal : autoTradeState.settings.fixed_margin_usd,
    equity_risk_pct: sizingMode === "pct_equity" ? marginVal : autoTradeState.settings.equity_risk_pct,
    min_confidence: parseFloat(atSettingMinConf?.value || "65.0"),
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
      if (autoTradeState.enabled) {
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

export function renderAutoTradeState() {
  const isEnabled = !!autoTradeState.enabled;
  const status = autoTradeState.status || "IDLE";
  const mode = (autoTradeState.settings?.mode || "demo").toUpperCase();
  const settings = autoTradeState.settings || {};
  const stats = autoTradeState.stats || {};
  const dailyPnl = stats.daily_realized_pnl || 0.0;
  const winRate = stats.win_rate || 0.0;
  const totalTrades = stats.total_trades || 0;

  // 1. Top Nav Button Update
  if (navAutoTradeDot) {
    navAutoTradeDot.className = `autotrade-status-dot ${isEnabled ? "active" : ""}`;
  }
  if (navAutoTradePill) {
    navAutoTradePill.textContent = isEnabled ? "ON" : "OFF";
    navAutoTradePill.className = `autotrade-pill ${isEnabled ? "on" : "off"}`;
  }

  // 2. Simple Mode Card Update
  if (btnSimpleAutoTradeToggle) {
    btnSimpleAutoTradeToggle.classList.toggle("active", isEnabled);
  }
  if (simpleAutoTradeToggleLabel) {
    simpleAutoTradeToggleLabel.textContent = isEnabled ? "ON" : "OFF";
  }
  if (simpleAtStatusDot) {
    simpleAtStatusDot.className = `status-pulse-dot ${isEnabled ? (status === "IN_POSITION" ? "busy" : "active") : ""}`;
  }
  if (simpleAtStatusText) {
    simpleAtStatusText.textContent = autoTradeState.status_message || "Standby";
  }
  if (simpleAtRiskVal) {
    const sizingStr = settings.sizing_mode === "pct_equity" ? `${settings.equity_risk_pct}% Eq` : `$${settings.fixed_margin_usd?.toFixed(2)}`;
    simpleAtRiskVal.textContent = `${settings.leverage || 50}x • ${sizingStr} Margin`;
  }
  if (simpleAtWinRate) {
    simpleAtWinRate.textContent = `${winRate}% (${totalTrades} trades)`;
  }

  // 3. Pro Mode Card Update
  if (btnProAutoTradeToggle) {
    btnProAutoTradeToggle.classList.toggle("active", isEnabled);
  }
  if (proAutoTradeToggleLabel) {
    proAutoTradeToggleLabel.textContent = isEnabled ? "ON" : "OFF";
  }
  if (proAtModeBadge) {
    proAtModeBadge.textContent = mode;
    proAtModeBadge.className = `at-mode-badge ${mode.toLowerCase()}`;
  }
  if (proAtStatus) {
    proAtStatus.textContent = status;
    proAtStatus.className = `val status-${status.toLowerCase()}`;
  }
  if (proAtSizing) {
    const sizingStr = settings.sizing_mode === "pct_equity" ? `${settings.equity_risk_pct}% Eq` : `$${settings.fixed_margin_usd?.toFixed(2)}`;
    proAtSizing.textContent = `${settings.leverage || 50}x • ${sizingStr}`;
  }
  if (proAtPnl) {
    const sign = dailyPnl >= 0 ? "+" : "";
    proAtPnl.textContent = `${sign}$${dailyPnl.toFixed(2)}`;
    proAtPnl.className = `val ${dailyPnl >= 0 ? "pnl-green" : "pnl-red"}`;
  }
  if (proAtCircuit) {
    const maxLoss = settings.max_daily_loss_usd || 5.0;
    const consec = stats.consecutive_losses || 0;
    const maxConsec = settings.max_consecutive_losses || 3;
    proAtCircuit.textContent = `${consec}/${maxConsec} loss (${sign(dailyPnl)}$${dailyPnl.toFixed(2)} / -$${maxLoss.toFixed(2)})`;
  }

  // 4. Modal Master Switch & Status Bar
  if (modalAutoTradeToggle) {
    modalAutoTradeToggle.classList.toggle("active", isEnabled);
  }
  if (modalAutoTradeToggleLabel) {
    modalAutoTradeToggleLabel.textContent = isEnabled ? "ON" : "OFF";
  }
  if (modalAtStatusPill) {
    modalAtStatusPill.className = `val-pill status-${status.toLowerCase()}`;
  }
  if (modalAtStatusText) {
    modalAtStatusText.textContent = status;
  }
  if (modalAtModeTag) {
    modalAtModeTag.textContent = `${mode} ${mode === "DEMO" ? "SIMULATION" : "LIVE"}`;
    modalAtModeTag.className = `val-tag ${mode.toLowerCase()}`;
  }
  if (modalAtWinRateVal) {
    modalAtWinRateVal.textContent = `${winRate}% (${totalTrades} trades)`;
  }
  if (modalAtDailyPnlVal) {
    const sign = dailyPnl >= 0 ? "+" : "";
    modalAtDailyPnlVal.textContent = `${sign}$${dailyPnl.toFixed(2)}`;
    modalAtDailyPnlVal.className = `val-bold ${dailyPnl >= 0 ? "pnl-green" : "pnl-red"}`;
  }

  // 5. Update Form Field Values if not focused
  if (atSettingMode && document.activeElement !== atSettingMode) {
    atSettingMode.value = settings.mode || "demo";
  }
  if (atSettingLeverage && document.activeElement !== atSettingLeverage) {
    atSettingLeverage.value = String(settings.leverage || 50);
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
  if (atSettingMinConf && document.activeElement !== atSettingMinConf) {
    atSettingMinConf.value = String(settings.min_confidence || 65);
  }
  if (atSettingCooldown && document.activeElement !== atSettingCooldown) {
    atSettingCooldown.value = String(settings.cooldown_seconds || 30);
  }
  if (atSettingAutoBracket) atSettingAutoBracket.checked = !!settings.auto_bracket;
  if (atSettingAutoBreakeven) atSettingAutoBreakeven.checked = !!settings.auto_breakeven;
  if (atSettingReversalExit) atSettingReversalExit.checked = !!settings.opposing_reversal_exit;

  if (atSettingMaxDailyLoss && document.activeElement !== atSettingMaxDailyLoss) {
    atSettingMaxDailyLoss.value = String(settings.max_daily_loss_usd || 5.0);
  }
  if (atSettingMaxConsec && document.activeElement !== atSettingMaxConsec) {
    atSettingMaxConsec.value = String(settings.max_consecutive_losses || 3);
  }

  if (safetyDailyPnl) {
    const sign = dailyPnl >= 0 ? "+" : "";
    safetyDailyPnl.textContent = `${sign}$${dailyPnl.toFixed(2)}`;
    safetyDailyPnl.className = `sm-val ${dailyPnl >= 0 ? "pnl-green" : "pnl-red"}`;
  }
  if (safetyDailyLimitLabel) {
    safetyDailyLimitLabel.textContent = `Cap: -$${(settings.max_daily_loss_usd || 5.0).toFixed(2)} USDT`;
  }
  if (safetyConsecLosses) {
    safetyConsecLosses.textContent = `${stats.consecutive_losses || 0} / ${settings.max_consecutive_losses || 3}`;
  }

  // 6. Render Logs & Log Snippet
  renderLogs();
}

function renderLogs() {
  const logs = autoTradeState.logs || [];

  // Update Pro card log snippet (latest log item)
  if (proAtLogSnippet) {
    if (logs.length > 0) {
      const top = logs[0];
      proAtLogSnippet.innerHTML = `
        <div class="snippet-item ${top.level}">
          <span class="snippet-time">${top.time}</span>
          <span class="snippet-cat">[${top.category}]</span>
          <span class="snippet-msg">${top.message}</span>
        </div>
      `;
    } else {
      proAtLogSnippet.innerHTML = `
        <div class="snippet-item">
          <span class="snippet-time">--:--:--</span>
          <span class="snippet-cat">[SYSTEM]</span>
          <span class="snippet-msg">Auto-trading standing by...</span>
        </div>
      `;
    }
  }

  // Update Full Modal Terminal Box
  if (atTerminalBox) {
    if (logs.length === 0) {
      atTerminalBox.innerHTML = `
        <div class="log-line info">
          <span class="log-time">--:--:--</span>
          <span class="log-badge system">SYSTEM</span>
          <span class="log-msg">Audit terminal ready. Awaiting events...</span>
        </div>
      `;
      return;
    }

    atTerminalBox.innerHTML = logs.map((l) => `
      <div class="log-line ${l.level || 'info'}">
        <span class="log-time">${l.time}</span>
        <span class="log-badge ${(l.category || 'system').toLowerCase()}">${l.category}</span>
        <span class="log-msg">${escapeHtml(l.message)}</span>
      </div>
    `).join("");
  }
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function sign(val) {
  return val >= 0 ? "+" : "";
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
  btnSimpleAtConfig?.addEventListener("click", () => {
    openAutoTradeModal();
    switchModalTab("settings");
  });
  btnProAtConfig?.addEventListener("click", () => {
    openAutoTradeModal();
    switchModalTab("settings");
  });

  autoTradeModal?.addEventListener("click", (e) => {
    if (e.target === autoTradeModal) closeAutoTradeModal();
  });

  // Master Switch Toggles
  btnSimpleAutoTradeToggle?.addEventListener("click", () => toggleAutoTrade());
  btnProAutoTradeToggle?.addEventListener("click", () => toggleAutoTrade());
  modalAutoTradeToggle?.addEventListener("click", () => toggleAutoTrade());

  // Emergency Stops
  btnSimpleAtKill?.addEventListener("click", executeEmergencyStop);
  btnProAtKill?.addEventListener("click", executeEmergencyStop);
  btnModalEmergencyStop?.addEventListener("click", executeEmergencyStop);

  // Tabs
  tabAtSettings?.addEventListener("click", () => switchModalTab("settings"));
  tabAtSafety?.addEventListener("click", () => switchModalTab("safety"));
  tabAtLogs?.addEventListener("click", () => switchModalTab("logs"));

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

  // Periodic status poll (every 6 seconds)
  if (pollInterval) clearInterval(pollInterval);
  pollInterval = setInterval(() => {
    if (document.hidden) return;
    fetchAutoTradeStatus();
  }, 6000);
}
