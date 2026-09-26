// ========================================================
// TOOBIT ACCOUNT & POSITION MANAGEMENT CONTROLLER
// Market-view dependencies (symbol, fallback price, chart
// overlays) are injected once via initToobitManager(deps)
// so this module never imports from the app entry point.
// ========================================================

import { formatPrice, formatUsd } from "./format.js";
import { showToast, playSignalChime } from "./feedback.js";

let deps = {
  getSymbol: () => "1000PEPEUSDT.P",
  getFallbackPrice: () => null,
  updateChartPositionLines: () => {},
  clearPositionLines: () => {},
  switchSymbol: () => {},
};

// DOM Elements — Toobit modal & nav
const btnToobitConnect = document.getElementById("btnToobitConnect");
const toobitNavStatus = document.getElementById("toobitNavStatus");
const toobitModal = document.getElementById("toobitModal");
const btnCloseToobitModal = document.getElementById("btnCloseToobitModal");
const modalStatusDot = document.getElementById("modalStatusDot");
const modalStatusText = document.getElementById("modalStatusText");
const modalStatusSub = document.getElementById("modalStatusSub");
const modalAvailableBal = document.getElementById("modalAvailableBal");
const tabModeLive = document.getElementById("tabModeLive");
const tabModeDemo = document.getElementById("tabModeDemo");
const tabContentLive = document.getElementById("tabContentLive");
const tabContentDemo = document.getElementById("tabContentDemo");
const toobitApiForm = document.getElementById("toobitApiForm");
const toobitApiKey = document.getElementById("toobitApiKey");
const toobitApiSecret = document.getElementById("toobitApiSecret");
const btnToggleSecret = document.getElementById("btnToggleSecret");
const eyeIcon = document.getElementById("eyeIcon");
const btnSaveToobitKeys = document.getElementById("btnSaveToobitKeys");
const btnDisconnectToobit = document.getElementById("btnDisconnectToobit");
const btnActivateDemoMode = document.getElementById("btnActivateDemoMode");
const toobitConnectError = document.getElementById("toobitConnectError");
const toobitConnectErrorText = document.getElementById("toobitConnectErrorText");

// Simple Mode Position Elements
const simplePositionCard = document.getElementById("simplePositionCard");
const btnSimpleFlashClose = document.getElementById("btnSimpleFlashClose");
const simpleSugBadge = document.getElementById("simpleSugBadge");
const simpleSugHeadline = document.getElementById("simpleSugHeadline");
const simpleSugText = document.getElementById("simpleSugText");
const btnSimpleSimLong = document.getElementById("btnSimpleSimLong");
const btnSimpleSimShort = document.getElementById("btnSimpleSimShort");

// Pro Mode Position Elements
const proPositionCard = document.getElementById("proPositionCard");
const proPosModeBadge = document.getElementById("proPosModeBadge");
const btnProFlashClose = document.getElementById("btnProFlashClose");
const proSugBadge = document.getElementById("proSugBadge");
const proAlignmentPill = document.getElementById("proAlignmentPill");
const proSugSl = document.getElementById("proSugSl");
const proSugTp = document.getElementById("proSugTp");
const proSugText = document.getElementById("proSugText");
const btnProSimLong = document.getElementById("btnProSimLong");
const btnProSimShort = document.getElementById("btnProSimShort");

// Shared chart-adjacent controls this module wires up
const chartPositionBadge = document.getElementById("chartPositionBadge");
const btnHudFlashClose = document.getElementById("btnHudFlashClose");
const togglePositionLines = document.getElementById("togglePositionLines");

let toobitAccountStatus = {
  connected: false,
  mode: "demo",
  balance: {}
};
let activeToobitPositions = [];
let currentPositionSuggestion = null;
let toobitPollInterval = null;

function showToobitConnectError(message) {
  if (!toobitConnectError || !toobitConnectErrorText) return;
  toobitConnectErrorText.textContent = message;
  toobitConnectError.classList.remove("hide");
  if (typeof lucide !== "undefined" && lucide.createIcons) {
    lucide.createIcons();
  }
}

function hideToobitConnectError() {
  if (!toobitConnectError) return;
  toobitConnectError.classList.add("hide");
  if (toobitConnectErrorText) toobitConnectErrorText.textContent = "";
}

// <dialog>.showModal() supplies Esc-to-close, the focus trap and focus restore on close.
function openToobitModal() {
  if (!toobitModal || toobitModal.open) return;
  hideToobitConnectError();
  toobitModal.showModal();
  fetchToobitStatus();
}

function closeToobitModal() {
  toobitModal?.close();
}

toobitModal?.addEventListener("close", hideToobitConnectError);

// Modal Tab Switcher
tabModeLive?.addEventListener("click", () => {
  hideToobitConnectError();
  tabModeLive.classList.add("active");
  tabModeLive.setAttribute("aria-selected", "true");
  tabModeDemo?.classList.remove("active");
  tabModeDemo?.setAttribute("aria-selected", "false");
  tabContentLive?.classList.remove("hide");
  tabContentLive?.classList.add("active");
  tabContentDemo?.classList.add("hide");
  tabContentDemo?.classList.remove("active");
});

tabModeDemo?.addEventListener("click", () => {
  hideToobitConnectError();
  tabModeDemo.classList.add("active");
  tabModeDemo.setAttribute("aria-selected", "true");
  tabModeLive?.classList.remove("active");
  tabModeLive?.setAttribute("aria-selected", "false");
  tabContentDemo?.classList.remove("hide");
  tabContentDemo?.classList.add("active");
  tabContentLive?.classList.add("hide");
  tabContentLive?.classList.remove("active");
});

// Toggle Secret Eye
btnToggleSecret?.addEventListener("click", () => {
  if (toobitApiSecret) {
    const isPassword = toobitApiSecret.type === "password";
    toobitApiSecret.type = isPassword ? "text" : "password";
    eyeIcon?.setAttribute("data-lucide", isPassword ? "eye-off" : "eye");
    lucide.createIcons();
  }
});

// Fetch Connection & Balance Status
async function fetchToobitStatus() {
  try {
    const res = await fetch("/api/toobit/status");
    if (!res.ok) return;
    const data = await res.json();
    toobitAccountStatus = data;

    // Update Nav status pill
    if (toobitNavStatus) {
      toobitNavStatus.className = `toobit-nav-pill ${data.mode}`;
      if (data.mode === "live") {
        toobitNavStatus.textContent = "Live";
      } else if (data.mode === "demo") {
        toobitNavStatus.textContent = "Demo";
      } else {
        toobitNavStatus.textContent = "Offline";
      }
    }

    // Update Modal Banner
    if (modalStatusDot && modalStatusText && modalAvailableBal) {
      modalStatusDot.className = `status-indicator-dot ${data.mode}`;
      if (data.mode === "live") {
        modalStatusText.textContent = "Live Toobit API Connected";
        if (modalStatusSub) modalStatusSub.textContent = "Real futures funds active";
        modalAvailableBal.textContent = `${formatUsd(data.balance?.availableBalance || 0)} USDT`;
      } else if (data.mode === "demo") {
        modalStatusText.textContent = "Simulated Demo Mode Active";
        if (modalStatusSub) modalStatusSub.textContent = "Trading with virtual $5,000 USDT";
        modalAvailableBal.textContent = `${formatUsd(data.balance?.availableBalance || 5000)} USDT`;
      } else {
        modalStatusText.textContent = "Not Connected";
        if (modalStatusSub) modalStatusSub.textContent = "Enter API keys or use Demo Mode";
        modalAvailableBal.textContent = "$0.00 USDT";
      }
    }

    if (proPosModeBadge) {
      proPosModeBadge.textContent = data.mode === "live" ? "Live" : "Demo";
      proPosModeBadge.className = `badge-mini ${data.mode === "live" ? "badge-live" : "badge-demo"}`;
    }
  } catch (e) {
    console.warn("Failed to fetch Toobit status:", e);
  }
}

// Helper: Check if an active position matches a chart symbol
function posMatchesChart(pos, chartSym) {
  if (!pos || !chartSym) return false;
  const normPos = (pos.rawSymbol || pos.symbol || pos.displaySymbol || "")
    .toUpperCase()
    .replace(/[-_.]/g, "")
    .replace("SWAP", "")
    .replace("P", "");
  const normChart = chartSym
    .toUpperCase()
    .replace(/[-_.]/g, "")
    .replace("SWAP", "")
    .replace("P", "");
  return normPos === normChart;
}

// Fetch Positions and AI Suggestions
export async function fetchToobitPositions() {
  try {
    const currentSym = deps.getSymbol();
    // 1. Query all positions across the user's Toobit account (no symbol filtering)
    const posRes = await fetch("/api/toobit/positions");

    let allPositions = [];
    if (posRes.ok) {
      const posData = await posRes.json();
      allPositions = posData.positions || posData.all_positions || [];
    }

    // 2. Identify if an active position matches the currently viewed chart symbol
    const matchingPos = allPositions.find((p) => posMatchesChart(p, currentSym));
    // If not matching current chart, prioritize the user's open position (e.g. SOL)
    const activePos = matchingPos || (allPositions.length > 0 ? allPositions[0] : null);
    const positions = activePos ? [activePos] : [];
    activeToobitPositions = positions;

    // 3. Fetch suggestion for active position's symbol (or current chart coin if no position)
    const sugSym = activePos ? (activePos.displaySymbol || activePos.symbol) : currentSym;
    let suggestionData = null;
    try {
      const sugRes = await fetch(`/api/toobit/suggestions?symbol=${encodeURIComponent(sugSym)}`);
      if (sugRes.ok) {
        suggestionData = await sugRes.json();
        currentPositionSuggestion = suggestionData.suggestion;
      }
    } catch (e) {
      console.warn("Failed to fetch Toobit suggestion:", e);
    }

    renderToobitPositions(positions, suggestionData?.suggestion);
  } catch (e) {
    console.warn("Failed to fetch Toobit positions/suggestions:", e);
  }
}

// Live tick from the market websocket: keep the mark price fresh
export function handlePositionTick(price) {
  if (activeToobitPositions && activeToobitPositions.length > 0) {
    if (posMatchesChart(activeToobitPositions[0], deps.getSymbol())) {
      activeToobitPositions[0].markPrice = price;
      deps.updateChartPositionLines(activeToobitPositions, currentPositionSuggestion);
    }
  }
}

// Fill one position card (Simple or Pro) from a Toobit position, or show its empty state
function fillPositionCard(card, pos) {
  if (!card) return;
  const field = (name) => card.querySelector(`[data-pos="${name}"]`);
  const live = field("live");
  const empty = field("empty");
  const flashClose = card.querySelector(".btn-flash-close");

  if (!pos) {
    live?.classList.add("hide");
    empty?.classList.remove("hide");
    if (flashClose) flashClose.style.display = "none";
    return;
  }

  live?.classList.remove("hide");
  empty?.classList.add("hide");
  if (flashClose) flashClose.style.display = "inline-flex";

  const isLong = String(pos.side || "LONG").toUpperCase() === "LONG";
  const entry = Number(pos.entryPrice) || 0;
  const mark = Number(pos.markPrice) || Number(deps.getFallbackPrice()) || entry;
  const liq = Number(pos.liquidationPrice) || 0;
  const qty = Number(pos.quantity) || 0;
  const margin = Number(pos.margin) || 0;
  const leverage = Number(pos.leverage) || 50;
  const pnl = Number(pos.unrealizedPnl) || 0;
  const pnlPct = Number(pos.pnlPercentage) || 0;
  const profitable = pnl >= 0;

  const set = (name, text) => {
    const el = field(name);
    if (el) el.textContent = text;
  };
  const setPnl = (name, base, text) => {
    const el = field(name);
    if (el) {
      el.textContent = text;
      el.className = `${base} ${profitable ? "pnl-green" : "pnl-red"}`;
    }
  };

  // Identity
  const posDisplaySym = pos.displaySymbol || pos.symbol || deps.getSymbol();
  const cleanSym = posDisplaySym.replace("-SWAP-", "").replace(".P", "").replace("USDT", "");
  set("symbol", cleanSym);

  const sideEl = field("side");
  if (sideEl) {
    sideEl.textContent = leverage > 0 ? `${isLong ? "LONG" : "SHORT"} ${leverage}x` : isLong ? "LONG" : "SHORT";
    sideEl.className = `pos-badge ${isLong ? "long" : "short"}`;
  }
  const modeEl = field("mode");
  if (modeEl) {
    const isLive = toobitAccountStatus.mode === "live";
    modeEl.textContent = isLive ? "Live" : "Demo";
    modeEl.className = `badge-mini ${isLive ? "badge-live" : "badge-demo"}`;
  }

  // View on Chart switch button
  const switchBtn = card.querySelector('[data-pos="switchBtn"]');
  if (switchBtn) {
    const isChartMatch = posMatchesChart(pos, deps.getSymbol());
    if (!isChartMatch) {
      switchBtn.classList.remove("hide");
      switchBtn.textContent = `View ${cleanSym} Chart ↗`;
      switchBtn.onclick = (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (deps.switchSymbol) {
          deps.switchSymbol(pos.displaySymbol || pos.symbol);
        }
      };
    } else {
      switchBtn.classList.add("hide");
    }
  }

  // PnL hero
  const pctAbs = Math.abs(pnlPct).toFixed(1);
  setPnl("pnl", "pos-hero-pnl", formatUsd(pnl, { signed: true }));
  setPnl("pnlPct", "pos-hero-pct", pctAbs === "0.0" ? "0.0%" : `${pnlPct < 0 ? "-" : "+"}${pctAbs}%`);

  // Stats
  set("qty", qty.toLocaleString());
  set("notional", formatUsd(qty * mark));
  set("margin", formatUsd(margin));
  set("lev", leverage > 0 ? `${leverage}x` : "--");
  set("entryStat", entry > 0 ? `$${formatPrice(entry)}` : "--");
  set("mark", mark > 0 ? `$${formatPrice(mark)}` : "--");
  set("entry", entry > 0 ? `$${formatPrice(entry)}` : "--");
  set("liq", liq > 0 ? `$${formatPrice(liq)}` : "--");
  const posSl = Number(pos.stopLoss) || 0;
  const posTp = Number(pos.takeProfit || pos.takeProfit1) || 0;
  set("sl", posSl > 0 ? `$${formatPrice(posSl)}` : "--");
  set("tp", posTp > 0 ? `$${formatPrice(posTp)}` : "--");

  // Liquidation distance bar
  const pin = field("pin");
  const liqVal = field("liqDist");
  if (liq > 0 && entry > 0) {
    const span = Math.abs(2 * entry - liq - liq) || 1;
    const rawPct = isLong ? ((mark - liq) / span) * 100 : ((liq - mark) / span) * 100;
    const pct = Math.max(0, Math.min(100, rawPct));
    if (pin) {
      pin.style.left = `${pct}%`;
      pin.className = `pos-risk-pin ${pct < 15 ? "danger" : pct < 50 ? "warn" : "safe"}`;
    }
    const distPct = mark > 0 ? (Math.abs(mark - liq) / mark) * 100 : 0;
    if (liqVal) {
      liqVal.textContent = `${distPct.toFixed(1)}% away`;
      liqVal.className = `pos-risk-val${pct < 15 ? " danger" : pct < 50 ? " warn" : ""}`;
    }
  } else {
    if (pin) pin.style.left = "50%";
    if (liqVal) {
      liqVal.textContent = "n/a";
      liqVal.className = "pos-risk-val";
    }
  }
}

// Render Positions in Simple & Pro Mode
function renderToobitPositions(positions, suggestion) {
  const activePos = positions && positions.length > 0 ? positions[0] : null;

  // --- SIMPLE MODE RENDERING ---
  if (simplePositionCard) {
    fillPositionCard(simplePositionCard, activePos);

    // Simple Mode AI Suggestion
    if (suggestion) {
      if (simpleSugBadge) {
        simpleSugBadge.textContent = suggestion.badgeText || "WAIT ⚪";
        simpleSugBadge.className = `ai-sug-badge ${suggestion.badgeClass || "badge-neutral"}`;
      }
      if (simpleSugHeadline) simpleSugHeadline.textContent = suggestion.headline || "Evaluating Market...";
      if (simpleSugText) simpleSugText.textContent = suggestion.suggestion || "Stand by for high-probability confluence setup.";
    }
  }

  // --- PRO MODE RENDERING ---
  if (proPositionCard) {
    fillPositionCard(proPositionCard, activePos);

    // Pro Mode AI Suggestion
    if (suggestion) {
      if (proSugBadge) {
        proSugBadge.textContent = suggestion.badgeText || "WAIT ⚪";
        proSugBadge.className = `ai-sug-badge ${suggestion.badgeClass || "badge-neutral"}`;
      }
      if (proAlignmentPill) {
        if (suggestion.alignment === "ALIGNED") {
          proAlignmentPill.textContent = "Trend Aligned";
          proAlignmentPill.className = "alignment-pill aligned";
        } else if (suggestion.alignment === "OPPOSED") {
          proAlignmentPill.textContent = "Trend Opposed / Risk";
          proAlignmentPill.className = "alignment-pill opposed";
        } else {
          proAlignmentPill.textContent = "Neutral / Watch";
          proAlignmentPill.className = "alignment-pill neutral";
        }
      }
      if (proSugSl) {
        proSugSl.textContent = suggestion.recommendedSl > 0 ? `$${suggestion.recommendedSl.toFixed(4)}` : "--";
      }
      if (proSugTp) {
        proSugTp.textContent = suggestion.recommendedTp > 0 ? `$${suggestion.recommendedTp.toFixed(4)}` : "--";
      }
      if (proSugText) {
        proSugText.textContent = suggestion.suggestion || "Monitoring live order book and tick flow...";
      }
    }
  }

  // --- CHART POSITION LINES & FLOATING HUD ---
  // Only draw lines on chart if the position corresponds to the active chart coin!
  const isChartMatch = posMatchesChart(activePos, deps.getSymbol());
  if (isChartMatch) {
    deps.updateChartPositionLines(positions, suggestion);
  } else {
    deps.clearPositionLines();
    if (chartPositionBadge) chartPositionBadge.classList.add("hide");
  }
}

// Flash Close Execution
async function executeFlashClose() {
  try {
    const activePos = activeToobitPositions && activeToobitPositions.length > 0 ? activeToobitPositions[0] : null;
    const symbolToClose = activePos ? (activePos.rawSymbol || activePos.symbol || activePos.displaySymbol) : deps.getSymbol();
    showToast(`⚡ Sending Flash Close market order for ${symbolToClose}...`, "info");

    const res = await fetch("/api/toobit/close", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol: symbolToClose })
    });
    const data = await res.json();

    if (data.success) {
      showToast(data.message || `Position ${symbolToClose} successfully Flash Closed!`, "bull");
      playSignalChime(true);
      deps.clearPositionLines();
      if (chartPositionBadge) chartPositionBadge.classList.add("hide");
    } else {
      showToast(data.message || data.error || "Flash close failed", "error");
    }

    await fetchToobitPositions();
    await fetchToobitStatus();
  } catch (e) {
    showToast(`Error executing flash close: ${e.message}`, "error");
  }
}

// Demo Simulation
async function simulateDemoTrade(side) {
  try {
    const res = await fetch("/api/toobit/demo/simulate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        symbol: deps.getSymbol(),
        side: side,
        quantity: 100000,
        entryPrice: deps.getFallbackPrice() || null,
        leverage: 50
      })
    });
    const data = await res.json();
    if (data.success) {
      showToast(`Simulated ${side} 50x position opened for ${deps.getSymbol()}!`, "bull");
      playSignalChime(side === "LONG");
    }
    await fetchToobitPositions();
  } catch (e) {
    showToast(`Failed to simulate position: ${e.message}`, "error");
  }
}

// POST /api/toobit/connect and return the parsed JSON reply.
async function postToobitConnect(body) {
  const res = await fetch("/api/toobit/connect", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
  return res.json();
}

// Connect Live API
async function saveToobitCredentials(e) {
  e.preventDefault();
  const apiKey = toobitApiKey?.value.trim();
  const apiSecret = toobitApiSecret?.value.trim();

  if (!apiKey || !apiSecret) {
    showToobitConnectError("Please enter both Toobit API Key and Secret Key.");
    showToast("Please enter both API Key and Secret Key.", "error");
    return;
  }

  hideToobitConnectError();
  showToast("Validating API credentials with Toobit...", "info");
  if (btnSaveToobitKeys) {
    btnSaveToobitKeys.disabled = true;
    btnSaveToobitKeys.innerHTML = `<span>Validating...</span>`;
  }

  try {
    const data = await postToobitConnect({ apiKey, apiSecret, isDemo: false });

    if (data.success) {
      localStorage.setItem("toobit_api_key", apiKey);
      localStorage.setItem("toobit_api_secret", apiSecret);
      hideToobitConnectError();
      showToast("Toobit Futures API Connected Successfully!", "bull");
      closeToobitModal();
      await fetchToobitStatus();
      await fetchToobitPositions();
    } else {
      const errorMsg = data.message || data.error || data.detail || "Failed to validate Toobit API keys.";
      showToobitConnectError(errorMsg);
      showToast("Authentication Failed — see details in dialog", "error", 6000);
    }
  } catch (err) {
    const errorMsg = `Connection error: ${err.message}`;
    showToobitConnectError(errorMsg);
    showToast(errorMsg, "error", 6000);
  } finally {
    if (btnSaveToobitKeys) {
      btnSaveToobitKeys.disabled = false;
      btnSaveToobitKeys.innerHTML = `<i data-lucide="check-circle"></i><span>Connect & Validate API</span>`;
      lucide.createIcons();
    }
  }
}

// Disconnect
async function disconnectToobit() {
  try {
    localStorage.removeItem("toobit_api_key");
    localStorage.removeItem("toobit_api_secret");
    const res = await fetch("/api/toobit/disconnect", { method: "POST" });
    const data = await res.json();
    hideToobitConnectError();
    showToast("Disconnected from Toobit.", "info");
    if (toobitApiKey) toobitApiKey.value = "";
    if (toobitApiSecret) toobitApiSecret.value = "";
    await fetchToobitStatus();
    await fetchToobitPositions();
  } catch (e) {
    showToast(`Disconnect error: ${e.message}`, "error");
  }
}

// Activate Demo Mode
async function activateDemoMode() {
  try {
    await postToobitConnect({ isDemo: true });
    hideToobitConnectError();
    showToast("Demo Simulation Mode Activated ($5,000 USDT)!", "bull");
    closeToobitModal();
    await fetchToobitStatus();
    await fetchToobitPositions();
  } catch (e) {
    showToast(`Error activating demo mode: ${e.message}`, "error");
  }
}

// Initialize Toobit Listeners
export function initToobitManager(injectedDeps = {}) {
  deps = { ...deps, ...injectedDeps };

  btnToobitConnect?.addEventListener("click", openToobitModal);
  btnCloseToobitModal?.addEventListener("click", closeToobitModal);

  toobitModal?.addEventListener("click", (e) => {
    if (e.target === toobitModal) closeToobitModal();
  });

  toobitApiKey?.addEventListener("input", hideToobitConnectError);
  toobitApiSecret?.addEventListener("input", hideToobitConnectError);
  toobitApiForm?.addEventListener("submit", saveToobitCredentials);
  btnDisconnectToobit?.addEventListener("click", disconnectToobit);
  btnActivateDemoMode?.addEventListener("click", activateDemoMode);

  // Flash Close Listeners
  btnSimpleFlashClose?.addEventListener("click", executeFlashClose);
  btnProFlashClose?.addEventListener("click", executeFlashClose);
  btnHudFlashClose?.addEventListener("click", executeFlashClose);

  // Position Overlay Toggle Listener
  togglePositionLines?.addEventListener("change", () => {
    deps.updateChartPositionLines(activeToobitPositions, currentPositionSuggestion);
  });

  // Simulation Listeners
  btnSimpleSimLong?.addEventListener("click", () => simulateDemoTrade("LONG"));
  btnSimpleSimShort?.addEventListener("click", () => simulateDemoTrade("SHORT"));
  btnProSimLong?.addEventListener("click", () => simulateDemoTrade("LONG"));
  btnProSimShort?.addEventListener("click", () => simulateDemoTrade("SHORT"));

  // Auto-restore credentials from localStorage if present
  const savedKey = localStorage.getItem("toobit_api_key");
  const savedSecret = localStorage.getItem("toobit_api_secret");
  if (savedKey && savedSecret) {
    if (toobitApiKey) toobitApiKey.value = savedKey;
    if (toobitApiSecret) toobitApiSecret.value = savedSecret;
    // Re-login quietly (no toasts) if the server lost the live session, e.g. after a restart
    (async () => {
      const status = await (await fetch("/api/toobit/status")).json();
      if (status.connected && status.mode === "live") return;
      const data = await postToobitConnect({ apiKey: savedKey, apiSecret: savedSecret, isDemo: false });
      if (data.success) {
        fetchToobitStatus();
        fetchToobitPositions();
      }
    })().catch(() => {});
  }

  // Start periodic polling for positions (every 8 seconds to prevent API flooding)
  fetchToobitStatus();
  fetchToobitPositions();
  if (toobitPollInterval) clearInterval(toobitPollInterval);
  toobitPollInterval = setInterval(() => {
    if (document.hidden) return;
    fetchToobitPositions();
  }, 8000);
}
