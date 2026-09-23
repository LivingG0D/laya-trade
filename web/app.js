// ========================================================
// LAYA CRYPTO TRADINGVIEW CHART ANALYZER — APP.JS (CLEAN & DEDUPLICATED)
// ========================================================

let chart = null;
let candleSeries = null;
let volumeSeries = null;
let ema9Series = null;
let ema21Series = null;
let ema50Series = null;
let bbUpperSeries = null;
let bbLowerSeries = null;
let bbMiddleSeries = null;

let currentSymbol = "1000PEPEUSDT.P";
let currentInterval = "5m";
let ws = null;
let autoAnalyzeTimer = null;
let activePriceLines = [];
let chartMarkers = [];
let lastAnalyzedCandleTime = 0;
let lastKnownPrice = null;
let currentSignal = null;
let currentLatestTelemetry = null;
let audioEnabled = true;
let currentCalcAmount = 500;
let rawSignalHistory = [];
let activeHistoryFilter = "all";

// DOM Elements — Top Nav
const symbolSelect = document.getElementById("symbolSelect");
const tfButtons = document.querySelectorAll(".tf-btn");
const analyzeBtn = document.getElementById("analyzeBtn");
const toggleEMA = document.getElementById("toggleEMA");
const toggleBB = document.getElementById("toggleBB");
const toggleTargetLines = document.getElementById("toggleTargetLines");
const togglePositionLines = document.getElementById("togglePositionLines");
const toggleAutoAnalyze = document.getElementById("toggleAutoAnalyze");
const btnFitChart = document.getElementById("btnFitChart");
const btnFullscreenChart = document.getElementById("btnFullscreenChart");
const btnAudioToggle = document.getElementById("btnAudioToggle");
const audioIcon = document.getElementById("audioIcon");
const toastContainer = document.getElementById("toastContainer");
const chartLoading = document.getElementById("chartLoading");

// On-Chart Floating Position HUD Elements
const chartPositionBadge = document.getElementById("chartPositionBadge");
const posHudSide = document.getElementById("posHudSide");
const posHudSymbol = document.getElementById("posHudSymbol");
const posHudEntry = document.getElementById("posHudEntry");
const posHudPnl = document.getElementById("posHudPnl");
const posHudLiq = document.getElementById("posHudLiq");
const btnHudFlashClose = document.getElementById("btnHudFlashClose");

// Mode Switch Elements
const btnSimpleMode = document.getElementById("btnSimpleMode");
const btnProMode = document.getElementById("btnProMode");
const simpleModeContainer = document.getElementById("simpleModeContainer");
const proModeContainer = document.getElementById("proModeContainer");
let currentMode = localStorage.getItem("laya_mode") || "simple";

// Ticker Elements
const livePriceEl = document.getElementById("livePrice");
const priceChangeEl = document.getElementById("priceChange");
const high24hEl = document.getElementById("high24h");
const low24hEl = document.getElementById("low24h");
const volume24hEl = document.getElementById("volume24h");
const wsStatusText = document.getElementById("wsStatusText");
const statusDot = document.querySelector(".status-dot");
const priceFlash = document.getElementById("priceFlash");

// HUD Elements
const hudSymbol = document.getElementById("hudSymbol");
const hudInterval = document.getElementById("hudInterval");
const hudO = document.getElementById("hudO");
const hudH = document.getElementById("hudH");
const hudL = document.getElementById("hudL");
const hudC = document.getElementById("hudC");
const hudV = document.getElementById("hudV");
const hudEma9 = document.getElementById("hudEma9");
const hudEma21 = document.getElementById("hudEma21");
const hudEma50 = document.getElementById("hudEma50");
const hudRsi = document.getElementById("hudRsi");

// Simple Mode Elements
const simpleHeroCard = document.getElementById("simpleHeroCard");
const simpleHeroBadge = document.getElementById("simpleHeroBadge");
const simpleHeroIcon = document.getElementById("simpleHeroIcon");
const simpleHeroAction = document.getElementById("simpleHeroAction");
const simpleHeroSub = document.getElementById("simpleHeroSub");
const simpleConfidenceText = document.getElementById("simpleConfidenceText");
const simpleMeterBar = document.getElementById("simpleMeterBar");
const simpleRiskBadge = document.getElementById("simpleRiskBadge");

const simpleEntryPrice = document.getElementById("simpleEntryPrice");
const simpleTakeProfit = document.getElementById("simpleTakeProfit");
const simpleStopLoss = document.getElementById("simpleStopLoss");
const simpleTpDesc = document.getElementById("simpleTpDesc");
const simpleSlDesc = document.getElementById("simpleSlDesc");
const simpleReasonsList = document.getElementById("simpleReasonsList");
const btnCopyTradePlan = document.getElementById("btnCopyTradePlan");
const rrGainLabel = document.getElementById("rrGainLabel");
const rrRiskLabel = document.getElementById("rrRiskLabel");
const rrGainFill = document.getElementById("rrGainFill");
const rrRiskFill = document.getElementById("rrRiskFill");

// Position Calculator Elements
const calcPresetGroup = document.getElementById("calcPresetGroup");
const calcWinVal = document.getElementById("calcWinVal");
const calcLossVal = document.getElementById("calcLossVal");

// Pro Mode Signal Card Elements
const signalCard = document.getElementById("signalCard");
const signalBadge = document.getElementById("signalBadge");
const signalAction = document.getElementById("signalAction");
const confidenceVal = document.getElementById("confidenceVal");
const signalSubtext = document.getElementById("signalSubtext");
const targetEntry = document.getElementById("targetEntry");
const targetSL = document.getElementById("targetSL");
const targetBE = document.getElementById("targetBE");
const targetTP1 = document.getElementById("targetTP1");
const targetTP2 = document.getElementById("targetTP2");
const targetRRR = document.getElementById("targetRRR");
const targetHorizon = document.getElementById("targetHorizon");
const reasonsList = document.getElementById("reasonsList");
const latencyBadge = document.getElementById("latencyBadge");

// Simple Mode Break-Even Element
const simpleBreakEven = document.getElementById("simpleBreakEven");
const simpleBeDesc = document.getElementById("simpleBeDesc");

// Institutional Confluence HUD Strip Elements
const hudHtfVal = document.getElementById("hudHtfVal");
const hudHtfDot = document.getElementById("hudHtfDot");
const hudRegimeVal = document.getElementById("hudRegimeVal");
const hudObiVal = document.getElementById("hudObiVal");
const hudFundingVal = document.getElementById("hudFundingVal");

// Probabilities Elements
const probStrongLong = document.getElementById("probStrongLong");
const probScalpLong = document.getElementById("probScalpLong");
const probNeutral = document.getElementById("probNeutral");
const probScalpShort = document.getElementById("probScalpShort");
const probStrongShort = document.getElementById("probStrongShort");
const barStrongLong = document.getElementById("barStrongLong");
const barScalpLong = document.getElementById("barScalpLong");
const barNeutral = document.getElementById("barNeutral");
const barScalpShort = document.getElementById("barScalpShort");
const barStrongShort = document.getElementById("barStrongShort");
const squeezeRiskVal = document.getElementById("squeezeRiskVal");

// Telemetry & Gauge Elements
const telemRsi = document.getElementById("telemRsi");
const telemRsiState = document.getElementById("telemRsiState");
const telemTrend = document.getElementById("telemTrend");
const telemTrendState = document.getElementById("telemTrendState");
const telemMacd = document.getElementById("telemMacd");
const telemMacdState = document.getElementById("telemMacdState");
const telemVolSpike = document.getElementById("telemVolSpike");
const telemVolState = document.getElementById("telemVolState");
const telemSupport = document.getElementById("telemSupport");
const telemResistance = document.getElementById("telemResistance");
const telemAdx = document.getElementById("telemAdx");
const telemRegimeState = document.getElementById("telemRegimeState");
const telemSqueeze = document.getElementById("telemSqueeze");
const telemSqueezeState = document.getElementById("telemSqueezeState");
const telemObi = document.getElementById("telemObi");
const telemObiState = document.getElementById("telemObiState");
const telemHtf = document.getElementById("telemHtf");
const telemHtfState = document.getElementById("telemHtfState");
const gaugeSupport = document.getElementById("gaugeSupport");
const gaugePrice = document.getElementById("gaugePrice");
const gaugeResistance = document.getElementById("gaugeResistance");
const pivotGaugePin = document.getElementById("pivotGaugePin");

// History Drawer Elements
const historyDrawer = document.getElementById("historyDrawer");
const historyDrawerHeader = document.getElementById("historyDrawerHeader");
const drawerToggleBtn = document.getElementById("drawerToggleBtn");
const drawerChevron = document.getElementById("drawerChevron");
const historyTableBody = document.getElementById("historyTableBody");
const historyCount = document.getElementById("historyCount");
const btnExportHistory = document.getElementById("btnExportHistory");
const btnClearHistory = document.getElementById("btnClearHistory");
const histFilterButtons = document.querySelectorAll(".hist-filter-btn");

// Mobile Elements
const mainContent = document.getElementById("mainContent");
const mobileViewTabs = document.getElementById("mobileViewTabs");
const mobileTabButtons = document.querySelectorAll(".mobile-tab-btn");
const mobileTabSignalPill = document.getElementById("mobileTabSignalPill");
const mobileTabHistoryCount = document.getElementById("mobileTabHistoryCount");

// ========================================================
// Toobit & Positions Elements
// ========================================================
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
const simpleAiSuggestionBox = document.getElementById("simpleAiSuggestionBox");
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

let toobitAccountStatus = {
  connected: false,
  mode: "demo",
  balance: {}
};
let activeToobitPositions = [];
let currentPositionSuggestion = null;
let toobitPollInterval = null;

// --------------------------------------------------------
// Toast Alert Notification System
// --------------------------------------------------------
function showToast(message, type = "info", duration = null) {
  if (!toastContainer) return;
  const timeoutMs = duration !== null ? duration : (type === "error" ? 5000 : 2500);

  // Keep at most 4 toasts on screen
  while (toastContainer.children.length >= 4) {
    toastContainer.firstChild.remove();
  }

  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  const span = document.createElement("span");
  span.textContent = message;
  toast.appendChild(span);
  toastContainer.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateY(-10px)";
    toast.style.transition = "all 0.3s ease";
    setTimeout(() => toast.remove(), 300);
  }, timeoutMs);
}

// --------------------------------------------------------
// Audio Synthesizer Chimes
// --------------------------------------------------------
function playSignalChime(isLong) {
  if (!audioEnabled) return;
  try {
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (!AudioContext) return;
    const ctx = new AudioContext();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();

    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.type = "sine";
    const now = ctx.currentTime;

    if (isLong) {
      osc.frequency.setValueAtTime(587.33, now); // D5
      osc.frequency.exponentialRampToValueAtTime(880.0, now + 0.15); // A5
    } else {
      osc.frequency.setValueAtTime(659.25, now); // E5
      osc.frequency.exponentialRampToValueAtTime(440.0, now + 0.15); // A4
    }

    gain.gain.setValueAtTime(0.08, now);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.3);
    osc.start(now);
    osc.stop(now + 0.3);
  } catch (e) {}
}

// --------------------------------------------------------
// Initialize TradingView Lightweight Chart
// --------------------------------------------------------
function initChart() {
  const container = document.getElementById("chartContainer");
  if (!container) return;
  container.innerHTML = "";

  chart = LightweightCharts.createChart(container, {
    layout: {
      background: { type: "solid", color: "#0b0e14" },
      textColor: "#94a3b8",
      fontSize: 11,
      fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, sans-serif",
    },
    grid: {
      vertLines: { color: "rgba(255, 255, 255, 0.03)" },
      horzLines: { color: "rgba(255, 255, 255, 0.03)" },
    },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: { color: "#3b82f6", width: 1, style: 3, labelBackgroundColor: "#1e293b" },
      horzLine: { color: "#3b82f6", width: 1, style: 3, labelBackgroundColor: "#1e293b" },
    },
    rightPriceScale: {
      borderColor: "#242b3b",
      autoScale: true,
      alignLabels: true,
      scaleMargins: {
        top: 0.1,    // 10% breathing room at the top
        bottom: 0.25, // 25% room at the bottom so candles stay ABOVE volume and markers
      },
    },
    timeScale: {
      borderColor: "#242b3b",
      timeVisible: true,
      secondsVisible: false,
    },
  });

  // Candlestick Series
  candleSeries = chart.addCandlestickSeries({
    upColor: "#0ecb81",
    downColor: "#f6465d",
    borderVisible: false,
    wickUpColor: "#0ecb81",
    wickDownColor: "#f6465d",
    priceFormat: {
      type: "price",
      precision: currentSymbol.includes("PEPE") ? 7 : 4,
      minMove: currentSymbol.includes("PEPE") ? 0.0000001 : 0.0001,
    },
  });

  // Volume Series on its own isolated scale strictly at bottom 15%
  volumeSeries = chart.addHistogramSeries({
    priceFormat: { type: "volume" },
    priceScaleId: "volume_scale",
  });

  chart.priceScale("volume_scale").applyOptions({
    scaleMargins: {
      top: 0.85, // Volume is restricted to the bottom 15% of chart
      bottom: 0,
    },
    visible: false,
  });

  // Technical EMA Lines (Thin 1px lines, clean and subtle)
  ema9Series = chart.addLineSeries({
    color: "rgba(240, 185, 11, 0.75)",
    lineWidth: 1,
    title: "EMA 9",
    priceScaleId: "right",
    visible: currentMode === "pro",
  });

  ema21Series = chart.addLineSeries({
    color: "rgba(0, 230, 118, 0.75)",
    lineWidth: 1,
    title: "EMA 21",
    priceScaleId: "right",
    visible: currentMode === "pro",
  });

  ema50Series = chart.addLineSeries({
    color: "rgba(0, 176, 255, 0.6)",
    lineWidth: 1,
    lineStyle: 2,
    title: "EMA 50",
    priceScaleId: "right",
    visible: false, // Off by default to avoid visual clutter
  });

  // Bollinger Bands (Off by default to eliminate clutter)
  bbUpperSeries = chart.addLineSeries({
    color: "rgba(168, 85, 247, 0.5)",
    lineWidth: 1,
    lineStyle: 2,
    title: "BB Upper",
    priceScaleId: "right",
    visible: false,
  });

  bbLowerSeries = chart.addLineSeries({
    color: "rgba(168, 85, 247, 0.5)",
    lineWidth: 1,
    lineStyle: 2,
    title: "BB Lower",
    priceScaleId: "right",
    visible: false,
  });

  bbMiddleSeries = chart.addLineSeries({
    color: "rgba(168, 85, 247, 0.3)",
    lineWidth: 1,
    lineStyle: 3,
    title: "BB Mid",
    priceScaleId: "right",
    visible: false,
  });

  // Resize Observer
  const resizeObserver = new ResizeObserver((entries) => {
    if (!entries || entries.length === 0) return;
    const { width, height } = entries[0].contentRect;
    if (width > 0 && height > 0 && chart) {
      chart.applyOptions({ width, height });
    }
  });
  resizeObserver.observe(container);

  // Crosshair move listener to update HUD
  chart.subscribeCrosshairMove((param) => {
    if (!param.time || !param.seriesData) return;
    const candle = param.seriesData.get(candleSeries);
    const vol = param.seriesData.get(volumeSeries);
    const e9 = param.seriesData.get(ema9Series);
    const e21 = param.seriesData.get(ema21Series);
    const e50 = param.seriesData.get(ema50Series);

    if (candle) {
      if (hudO) hudO.textContent = formatPrice(candle.open);
      if (hudH) hudH.textContent = formatPrice(candle.high);
      if (hudL) hudL.textContent = formatPrice(candle.low);
      if (hudC) {
        hudC.textContent = formatPrice(candle.close);
        hudC.className = candle.close >= candle.open ? "price-up" : "price-down";
      }
    }
    if (vol && hudV) {
      hudV.textContent = formatCompact(vol.value);
    }
    if (e9 && hudEma9) hudEma9.textContent = formatPrice(e9.value);
    if (e21 && hudEma21) hudEma21.textContent = formatPrice(e21.value);
    if (e50 && hudEma50) hudEma50.textContent = formatPrice(e50.value);
  });
}

// --------------------------------------------------------
// Formatters
// --------------------------------------------------------
function formatPrice(val) {
  if (val === null || val === undefined || isNaN(val)) return "--";
  if (val < 0.01) {
    // 7 decimals is what the chart uses; trim trailing zeros for readability
    return Number(val).toFixed(7).replace(/0+$/, "").replace(/\.$/, "");
  }
  if (val < 1.0) return Number(val).toFixed(4);
  return Number(val).toFixed(2);
}

function formatCompact(val) {
  if (!val) return "0";
  if (val >= 1e9) return (val / 1e9).toFixed(2) + "B";
  if (val >= 1e6) return (val / 1e6).toFixed(2) + "M";
  if (val >= 1e3) return (val / 1e3).toFixed(2) + "K";
  return Number(val).toFixed(0);
}

function formatTimestamp(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// --------------------------------------------------------
// Clean Deduplicated Marker Manager
// --------------------------------------------------------
function applyDeduplicatedMarkers(inputMarkers = null) {
  if (!candleSeries) return;

  if (Array.isArray(inputMarkers)) {
    chartMarkers = inputMarkers.slice();
  } else if (inputMarkers && typeof inputMarkers === "object") {
    chartMarkers.push(inputMarkers);
  }

  // Deduplicate strictly by candle timestamp: only ONE marker per candle bar!
  const markerMap = new Map();
  chartMarkers.forEach((m) => {
    if (m && m.time) {
      const isLong = (m.shape === "arrowUp") || (m.text && m.text.includes("LONG")) || (m.text && m.text.includes("BUY"));
      markerMap.set(m.time, {
        time: m.time,
        position: isLong ? "belowBar" : "aboveBar",
        color: isLong ? "#0ecb81" : "#f6465d",
        shape: isLong ? "arrowUp" : "arrowDown",
        text: isLong ? "BUY" : "SELL",
        size: 1, // Compact, sharp size
      });
    }
  });

  // Sort strictly ascending by time (MANDATORY for TradingView Lightweight Charts)
  chartMarkers = Array.from(markerMap.values()).sort((a, b) => a.time - b.time);

  // Keep at most the last 8 markers so the chart never gets crowded
  if (chartMarkers.length > 8) {
    chartMarkers = chartMarkers.slice(-8);
  }

  candleSeries.setMarkers(chartMarkers);
}

// --------------------------------------------------------
// Load Historical Klines & Indicators
// --------------------------------------------------------
async function loadKlines(symbol, interval) {
  if (chartLoading) chartLoading.classList.remove("hide");
  try {
    if (hudSymbol) hudSymbol.textContent = symbol;
    if (hudInterval) hudInterval.textContent = interval;

    const resp = await fetch(`/api/klines?symbol=${encodeURIComponent(symbol)}&interval=${interval}&limit=350`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();

    const series = data.series || [];
    if (series.length === 0) throw new Error("Empty series");

    // Deduplicate and ensure strict ascending order by timestamp for TradingView Lightweight Charts
    const timeMap = new Map();
    series.forEach((bar) => {
      if (bar && typeof bar.time === "number" && !isNaN(bar.time)) {
        timeMap.set(bar.time, bar);
      }
    });
    const sortedSeries = Array.from(timeMap.values()).sort((a, b) => a.time - b.time);
    if (sortedSeries.length === 0) throw new Error("No valid bars in series");

    // Separate series data
    const candles = [];
    const volumes = [];
    const ema9 = [];
    const ema21 = [];
    const ema50 = [];
    const bbUp = [];
    const bbLow = [];
    const bbMid = [];

    sortedSeries.forEach((bar) => {
      candles.push({
        time: bar.time,
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
      });

      // Subtle translucent volume bars (20% opacity) that never obstruct candles
      volumes.push({
        time: bar.time,
        value: bar.volume,
        color: bar.close >= bar.open ? "rgba(14, 203, 129, 0.2)" : "rgba(246, 70, 93, 0.2)",
      });

      if (bar.ema9 !== null && !isNaN(bar.ema9)) ema9.push({ time: bar.time, value: bar.ema9 });
      if (bar.ema21 !== null && !isNaN(bar.ema21)) ema21.push({ time: bar.time, value: bar.ema21 });
      if (bar.ema50 !== null && !isNaN(bar.ema50)) ema50.push({ time: bar.time, value: bar.ema50 });

      if (bar.bb_upper !== null && !isNaN(bar.bb_upper)) bbUp.push({ time: bar.time, value: bar.bb_upper });
      if (bar.bb_lower !== null && !isNaN(bar.bb_lower)) bbLow.push({ time: bar.time, value: bar.bb_lower });
      if (bar.bb_middle !== null && !isNaN(bar.bb_middle)) bbMid.push({ time: bar.time, value: bar.bb_middle });
    });

    if (candleSeries) candleSeries.setData(candles);
    if (volumeSeries) volumeSeries.setData(volumes);
    if (ema9Series) ema9Series.setData(ema9);
    if (ema21Series) ema21Series.setData(ema21);
    if (ema50Series) ema50Series.setData(ema50);
    if (bbUpperSeries) bbUpperSeries.setData(bbUp);
    if (bbLowerSeries) bbLowerSeries.setData(bbLow);
    if (bbMiddleSeries) bbMiddleSeries.setData(bbMid);

    // Apply markers from history (Deduplicated & Cleaned)
    if (data.markers && data.markers.length > 0) {
      applyDeduplicatedMarkers(data.markers);
    } else {
      chartMarkers = [];
      if (candleSeries) candleSeries.setMarkers([]);
    }

    // Update Telemetry & HUD
    if (data.latest) {
      currentLatestTelemetry = data.latest;
      updateTelemetry(data.latest);
      updateLivePriceDisplay(data.latest.price, data.latest.change_pct);
    }

    if (chart) {
      chart.timeScale().fitContent();
    }
    load24hTicker(symbol);
  } catch (err) {
    console.error("Error loading klines:", err);
    showToast("Reconnecting to market data stream...", "warning");
    if (!window._klineRetryTimeout) {
      window._klineRetryTimeout = setTimeout(() => {
        window._klineRetryTimeout = null;
        loadKlines(symbol, interval);
      }, 2500);
    }
  } finally {
    if (chartLoading) chartLoading.classList.add("hide");
  }
}

// --------------------------------------------------------
// Fetch 24h Ticker Stats
// --------------------------------------------------------
async function load24hTicker(symbol) {
  try {
    const resp = await fetch(`/api/ticker?symbol=${encodeURIComponent(symbol)}`);
    if (!resp.ok) return;
    const t = await resp.json();
    if (t.last_price !== undefined) {
      updateLivePriceDisplay(t.last_price, t.price_change_percent);
    }
    if (t.high_price !== undefined && high24hEl) {
      high24hEl.textContent = formatPrice(t.high_price);
    }
    if (t.low_price !== undefined && low24hEl) {
      low24hEl.textContent = formatPrice(t.low_price);
    }
    if (t.quote_volume && volume24hEl) {
      volume24hEl.textContent = "$" + formatCompact(t.quote_volume);
    } else if (t.volume && volume24hEl) {
      volume24hEl.textContent = formatCompact(t.volume);
    }
  } catch (e) {
    console.warn("Ticker fetch error:", e);
  }
}

// --------------------------------------------------------
// Update Telemetry HUD & Pivot Range Gauge
// --------------------------------------------------------
function updateTelemetry(latest) {
  if (!latest) return;
  if (telemRsi) telemRsi.textContent = latest.rsi ? latest.rsi.toFixed(1) : "--";
  if (hudRsi) hudRsi.textContent = latest.rsi ? latest.rsi.toFixed(1) : "--";

  if (telemRsiState) {
    if (latest.rsi > 70) {
      telemRsiState.textContent = "Overbought";
      telemRsiState.style.color = "var(--red-down)";
    } else if (latest.rsi < 30) {
      telemRsiState.textContent = "Oversold";
      telemRsiState.style.color = "var(--green-up)";
    } else if (latest.rsi >= 50) {
      telemRsiState.textContent = "Bullish Tilt";
      telemRsiState.style.color = "var(--cyan-accent)";
    } else {
      telemRsiState.textContent = "Bearish Tilt";
      telemRsiState.style.color = "var(--yellow-accent)";
    }
  }

  if (telemTrend) telemTrend.textContent = latest.trend_state || "NEUTRAL";
  if (telemTrendState) telemTrendState.textContent = latest.trend_state ? latest.trend_state.replace("_", " ") : "Consolidation";

  if (telemMacd) telemMacd.textContent = latest.macd_hist ? (latest.macd_hist > 0 ? "+" : "") + latest.macd_hist.toFixed(6) : "0.0";
  if (telemMacdState) telemMacdState.textContent = latest.macd_hist > 0 ? "Bullish Exp" : "Bearish Exp";

  if (telemVolSpike) telemVolSpike.textContent = latest.vol_spike ? latest.vol_spike.toFixed(2) + "x" : "1.0x";
  if (telemVolState) telemVolState.textContent = latest.vol_spike > 1.3 ? "Elevated Surge" : "Normal Flow";

  if (telemSupport) telemSupport.textContent = formatPrice(latest.support);
  if (telemResistance) telemResistance.textContent = formatPrice(latest.resistance);

  // Institutional ADX & Regime
  if (telemAdx) telemAdx.textContent = latest.adx !== undefined ? latest.adx.toFixed(1) : "--";
  if (telemRegimeState) {
    telemRegimeState.textContent = latest.market_regime ? latest.market_regime.replace("_", " ") : "Regime";
    telemRegimeState.style.color = latest.market_regime === "TRENDING" ? "var(--green-up)" : "var(--yellow-accent)";
  }

  // TTM Squeeze
  if (telemSqueeze) telemSqueeze.textContent = latest.squeeze_status ? latest.squeeze_status.replace("_", " ") : "Normal";
  if (telemSqueezeState) {
    telemSqueezeState.textContent = latest.is_squeeze ? "COMPRESSED 🟡" : (latest.squeeze_status === "SQUEEZE_FIRED" ? "FIRED 🚀" : "Normal Vol");
    telemSqueezeState.style.color = latest.is_squeeze ? "var(--yellow-accent)" : (latest.squeeze_status === "SQUEEZE_FIRED" ? "var(--green-up)" : "var(--text-muted)");
  }

  // Order Book Imbalance (OBI)
  if (telemObi) {
    const obiVal = latest.depth && latest.depth.imbalance_pct !== undefined ? latest.depth.imbalance_pct : 0;
    telemObi.textContent = (obiVal > 0 ? "+" : "") + obiVal.toFixed(1) + "%";
    telemObi.style.color = obiVal > 15 ? "var(--green-up)" : (obiVal < -15 ? "var(--red-down)" : "var(--text-main)");
  }
  if (telemObiState) {
    telemObiState.textContent = latest.depth ? (latest.depth.status ? latest.depth.status.replace("_", " ") : "Ratio " + (latest.depth.bid_ask_ratio || 1.0)) : "Order Flow";
  }

  // 1H Macro Trend Confluence
  if (telemHtf) {
    telemHtf.textContent = latest.htf && (latest.htf.trend || latest.htf.htf_trend) ? (latest.htf.trend || latest.htf.htf_trend).replace("_", " ") : "--";
    const htfTr = latest.htf && (latest.htf.trend || latest.htf.htf_trend);
    if (htfTr) {
      telemHtf.style.color = htfTr.includes("BULLISH") ? "var(--green-up)" : (htfTr.includes("BEARISH") ? "var(--red-down)" : "var(--text-main)");
    }
  }
  if (telemHtfState) {
    telemHtfState.textContent = latest.htf && latest.htf.rsi ? `1H RSI: ${latest.htf.rsi}` : "HTF Alignment";
  }

  // Top Chart HUD Confluence Strip
  if (hudHtfVal && latest.htf) {
    const tr = latest.htf.trend || latest.htf.htf_trend || "NEUTRAL";
    hudHtfVal.textContent = "1H: " + tr.replace("STRONG_", "").replace("_", " ");
    if (hudHtfDot) {
      hudHtfDot.className = "inst-pill-dot " + (tr.includes("BULLISH") ? "bullish" : tr.includes("BEARISH") ? "bearish" : "neutral");
    }
  }
  if (hudRegimeVal) {
    const reg = latest.market_regime ? latest.market_regime.replace("_", " ") : "Regime";
    const adxStr = latest.adx !== undefined ? ` (${latest.adx.toFixed(0)})` : "";
    hudRegimeVal.textContent = reg + adxStr;
    const regimePill = document.getElementById("hudRegimePill");
    if (regimePill) {
      if (latest.is_squeeze) regimePill.classList.add("active-squeeze");
      else regimePill.classList.remove("active-squeeze");
    }
  }
  if (hudObiVal && latest.depth) {
    const obi = latest.depth.imbalance_pct !== undefined ? latest.depth.imbalance_pct : 0;
    hudObiVal.textContent = "OBI: " + (obi > 0 ? "+" : "") + obi.toFixed(0) + "%";
    hudObiVal.style.color = obi > 15 ? "var(--green-up)" : (obi < -15 ? "var(--red-down)" : "inherit");
  }
  if (hudFundingVal && latest.funding) {
    const fPct = latest.funding.funding_rate_pct !== undefined ? latest.funding.funding_rate_pct : 0;
    hudFundingVal.textContent = "Funding: " + (fPct > 0 ? "+" : "") + fPct.toFixed(4) + "%";
  }

  // Update Pro Pivot Range Gauge
  if (gaugeSupport) gaugeSupport.textContent = formatPrice(latest.support);
  if (gaugePrice) gaugePrice.textContent = formatPrice(latest.price);
  if (gaugeResistance) gaugeResistance.textContent = formatPrice(latest.resistance);

  if (pivotGaugePin && latest.support && latest.resistance && latest.price) {
    const range = latest.resistance - latest.support;
    if (range > 0) {
      let pct = ((latest.price - latest.support) / range) * 100;
      pct = Math.max(5, Math.min(95, pct));
      pivotGaugePin.style.left = `${pct}%`;
    }
  }
}

function updateLivePriceDisplay(price, changePct) {
  if (price === undefined || price === null) return;
  const numPrice = Number(price);

  if (lastKnownPrice !== null && lastKnownPrice !== numPrice) {
    if (numPrice > lastKnownPrice) {
      livePriceEl.classList.remove("flash-down");
      livePriceEl.classList.add("flash-up");
      setTimeout(() => livePriceEl.classList.remove("flash-up"), 800);
    } else if (numPrice < lastKnownPrice) {
      livePriceEl.classList.remove("flash-up");
      livePriceEl.classList.add("flash-down");
      setTimeout(() => livePriceEl.classList.remove("flash-down"), 800);
    }
  }
  lastKnownPrice = numPrice;

  livePriceEl.textContent = formatPrice(numPrice);

  if (changePct !== undefined) {
    const isUp = changePct >= 0;
    priceChangeEl.textContent = (isUp ? "+" : "") + changePct.toFixed(2) + "%";
    priceChangeEl.className = "metric-value " + (isUp ? "price-up" : "price-down");
  }
}

// --------------------------------------------------------
// Apply Signal to Dashboard & Chart
// --------------------------------------------------------
function applySignal(signal, latest, laya) {
  if (!signal) return;
  currentSignal = signal;
  if (latest) currentLatestTelemetry = latest;

  const isLong = signal.action === "LONG";
  const isShort = signal.action === "SHORT";

  // Play audio chime if active
  if (isLong || isShort) {
    playSignalChime(isLong);
  }

  // Update Mobile Header Pill
  if (mobileTabSignalPill) {
    mobileTabSignalPill.textContent = signal.action;
    mobileTabSignalPill.style.background = isLong ? "var(--green-up)" : isShort ? "var(--red-down)" : "var(--bg-hover)";
    mobileTabSignalPill.style.color = isLong ? "#000" : "#fff";
  }

  // 1. Update Simple Mode UI
  updateSimpleModeUI(signal, latest, laya);

  // 2. Update Pro Signal Badge
  signalBadge.className = "signal-badge " + (isLong ? "long" : isShort ? "short" : "neutral");
  signalAction.textContent = signal.action;
  confidenceVal.textContent = signal.confidence_pct + "%";

  signalCard.className = "signal-card " + (isLong ? "long-glow" : isShort ? "short-glow" : "");

  signalSubtext.textContent = isLong
    ? `Strong Bullish Setup (${signal.signal_type}) with favorable Risk/Reward.`
    : isShort
    ? `High Conviction Bearish Setup (${signal.signal_type}) with invalidation anchor.`
    : "Market in chop/consolidation. Waiting for cleaner structure.";

  // 3. Targets
  targetEntry.textContent = formatPrice(signal.entry_price);
  targetSL.textContent = formatPrice(signal.stop_loss);
  if (targetBE) {
    const beVal = signal.break_even || (signal.targets && signal.targets.break_even);
    targetBE.textContent = beVal ? formatPrice(beVal) : "--";
  }
  targetTP1.textContent = formatPrice(signal.take_profit_1);
  targetTP2.textContent = formatPrice(signal.take_profit_2);
  targetRRR.textContent = "1 : " + signal.risk_reward_ratio;
  targetHorizon.textContent = signal.time_horizon ? signal.time_horizon : "Scalp";

  // 4. Confluence Reasons
  reasonsList.innerHTML = "";
  if (signal.reasons && signal.reasons.length > 0) {
    signal.reasons.forEach((r) => {
      const li = document.createElement("li");
      li.textContent = r;
      reasonsList.appendChild(li);
    });
  }

  // 5. Probabilities Bars
  if (laya && laya.probabilities) {
    const p = laya.probabilities;
    const sl = Math.round((p.STRONG_LONG || 0) * 100);
    const scl = Math.round((p.SCALP_LONG || 0) * 100);
    const nh = Math.round((p.NEUTRAL_HOLD || 0) * 100);
    const scs = Math.round((p.SCALP_SHORT || 0) * 100);
    const ss = Math.round((p.STRONG_SHORT || 0) * 100);

    probStrongLong.textContent = sl + "%";
    barStrongLong.style.width = sl + "%";

    probScalpLong.textContent = scl + "%";
    barScalpLong.style.width = scl + "%";

    probNeutral.textContent = nh + "%";
    barNeutral.style.width = nh + "%";

    probScalpShort.textContent = scs + "%";
    barScalpShort.style.width = scs + "%";

    probStrongShort.textContent = ss + "%";
    barStrongShort.style.width = ss + "%";

    latencyBadge.textContent = `⚡ ${laya.latency_ms || 25} ms`;

    // Squeeze Risk
    const sq = Math.round((laya.squeeze_risk || 0.1) * 100);
    const sqRating = sq > 60 ? "High Whipsaw" : sq > 30 ? "Moderate" : "Low Risk";
    squeezeRiskVal.textContent = `${sqRating} (${sq}%)`;
    squeezeRiskVal.style.color = sq > 60 ? "var(--red-down)" : sq > 30 ? "var(--yellow-accent)" : "var(--green-up)";
  }

  // 6. Draw Horizontal Price Target Lines on Chart (Only in Pro Mode)
  updateChartPriceLines(signal);

  // 7. Add Clean Deduplicated Chart Marker
  if (signal.marker) {
    applyDeduplicatedMarkers(signal.marker);
  }

  // 8. Insert to History Table
  if (signal.action !== "NEUTRAL") {
    addSignalToHistory(signal);
  }
}

// --------------------------------------------------------
// Price Target Horizontal Lines Management (Minimalist)
// --------------------------------------------------------
function updateChartPriceLines(signal) {
  clearPriceLines();

  // In Simple Mode, keep the chart completely clean without lines
  if (currentMode === "simple") return;
  if (!toggleTargetLines || !toggleTargetLines.checked) return;
  if (!signal || signal.action === "NEUTRAL") return;

  // Thin, clean, professional lines
  if (signal.entry_price) {
    activePriceLines.push(
      candleSeries.createPriceLine({
        price: signal.entry_price,
        color: "rgba(59, 130, 246, 0.75)",
        lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dotted,
        axisLabelVisible: true,
        title: "ENTRY",
      })
    );
  }

  if (signal.stop_loss) {
    activePriceLines.push(
      candleSeries.createPriceLine({
        price: signal.stop_loss,
        color: "rgba(246, 70, 93, 0.75)",
        lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: true,
        title: "SL",
      })
    );
  }

  if (signal.take_profit_1) {
    activePriceLines.push(
      candleSeries.createPriceLine({
        price: signal.take_profit_1,
        color: "rgba(14, 203, 129, 0.75)",
        lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: true,
        title: "TP",
      })
    );
  }
}

function clearPriceLines() {
  activePriceLines.forEach((line) => {
    try {
      candleSeries.removePriceLine(line);
    } catch (e) {}
  });
  activePriceLines = [];
}

// --------------------------------------------------------
// Open Position Price Lines & On-Chart HUD
// --------------------------------------------------------
let activePositionLines = [];

function clearPositionLines() {
  if (candleSeries && activePositionLines.length > 0) {
    activePositionLines.forEach((line) => {
      try {
        candleSeries.removePriceLine(line);
      } catch (e) {}
    });
  }
  activePositionLines = [];
}

function updateChartPositionLines(positions, suggestion) {
  clearPositionLines();

  const isEnabled = !togglePositionLines || togglePositionLines.checked;
  const activePos = positions && positions.length > 0 ? positions[0] : null;

  if (!isEnabled || !activePos || !activePos.entryPrice || Number(activePos.entryPrice) <= 0 || !candleSeries) {
    if (chartPositionBadge) chartPositionBadge.classList.add("hide");
    return;
  }

  const isLong = String(activePos.side || "LONG").toUpperCase() === "LONG";
  const entry = Number(activePos.entryPrice);
  const mark = Number(activePos.markPrice) || Number(lastKnownPrice) || entry;
  const liq = Number(activePos.liquidationPrice) || 0;
  const qty = Number(activePos.quantity) || 0;
  const leverage = Number(activePos.leverage) || 10;
  const margin = Number(activePos.margin) || (leverage > 0 ? (qty * entry) / leverage : 0);

  // Real-time PnL calculation
  let pnl = Number(activePos.unrealizedPnl);
  if (isNaN(pnl) || (pnl === 0 && mark !== entry)) {
    pnl = isLong ? (mark - entry) * qty : (entry - mark) * qty;
  }
  const pnlPct = margin > 0 ? (pnl / margin) * 100 : 0;
  const pnlSign = pnl >= 0 ? "+" : "-";
  const pnlAbs = Math.abs(pnl).toFixed(2);
  const pctAbs = Math.abs(pnlPct).toFixed(1);
  const distPct = mark > 0 && liq > 0 ? (Math.abs(mark - liq) / mark) * 100 : 0;

  // 1. Position Entry Price Line (Solid, prominent)
  const posColor = isLong ? "#10b981" : "#ef4444";
  const entryTitle = `POS ${isLong ? "LONG" : "SHORT"} ${leverage ? leverage + "x" : ""} (${pnlSign}$${pnlAbs} / ${pnlSign}${pctAbs}%)`;

  try {
    const entryLine = candleSeries.createPriceLine({
      price: entry,
      color: posColor,
      lineWidth: 2,
      lineStyle: LightweightCharts.LineStyle.Solid,
      axisLabelVisible: true,
      title: entryTitle,
    });
    activePositionLines.push(entryLine);
  } catch (e) {
    console.warn("Failed to create entry price line:", e);
  }

  // 2. Liquidation Price Line (Warning orange/amber dashed)
  if (liq > 0) {
    try {
      const liqLine = candleSeries.createPriceLine({
        price: liq,
        color: "#f59e0b",
        lineWidth: 2,
        lineStyle: LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: true,
        title: `LIQ @ $${formatPrice(liq)} (${distPct.toFixed(1)}% away)`,
      });
      activePositionLines.push(liqLine);
    } catch (e) {
      console.warn("Failed to create liq price line:", e);
    }
  }

  // 3. Recommended SL / TP lines if available
  if (suggestion) {
    if (suggestion.recommendedTp && Number(suggestion.recommendedTp) > 0) {
      try {
        const tpLine = candleSeries.createPriceLine({
          price: Number(suggestion.recommendedTp),
          color: "rgba(16, 185, 129, 0.8)",
          lineWidth: 1,
          lineStyle: LightweightCharts.LineStyle.Dotted,
          axisLabelVisible: true,
          title: "POS TP",
        });
        activePositionLines.push(tpLine);
      } catch (e) {}
    }

    if (suggestion.recommendedSl && Number(suggestion.recommendedSl) > 0) {
      try {
        const slLine = candleSeries.createPriceLine({
          price: Number(suggestion.recommendedSl),
          color: "rgba(239, 68, 68, 0.8)",
          lineWidth: 1,
          lineStyle: LightweightCharts.LineStyle.Dotted,
          axisLabelVisible: true,
          title: "POS SL",
        });
        activePositionLines.push(slLine);
      } catch (e) {}
    }
  }

  // 4. Update On-Chart Floating HUD
  if (chartPositionBadge) {
    chartPositionBadge.classList.remove("hide");
    chartPositionBadge.classList.toggle("short-pos", !isLong);
    if (posHudSide) {
      posHudSide.textContent = `${isLong ? "LONG" : "SHORT"} ${leverage ? leverage + "x" : ""}`;
      posHudSide.className = `pos-hud-side ${isLong ? "long" : "short"}`;
    }
    if (posHudSymbol) {
      posHudSymbol.textContent = activePos.displaySymbol || activePos.symbol || currentSymbol;
    }
    if (posHudEntry) {
      posHudEntry.textContent = `Entry: $${formatPrice(entry)}`;
    }
    if (posHudPnl) {
      posHudPnl.textContent = `${pnlSign}$${pnlAbs} (${pnlSign}${pctAbs}%)`;
      posHudPnl.className = `pos-hud-pnl ${pnl >= 0 ? "pnl-green" : "pnl-red"}`;
    }
    if (posHudLiq) {
      posHudLiq.textContent = liq > 0 ? `Liq: $${formatPrice(liq)} (${distPct.toFixed(1)}% away)` : "Liq: N/A";
    }
  }
}

// --------------------------------------------------------
// Simple Mode Logic & UI Updater
// --------------------------------------------------------
function updateSimpleModeUI(signal, latest, laya) {
  const isLong = signal.action === "LONG";
  const isShort = signal.action === "SHORT";

  // 1. Hero Decision Card
  simpleHeroCard.className = "simple-hero-card " + (isLong ? "buy-active" : isShort ? "sell-active" : "");
  simpleHeroBadge.className = "simple-hero-badge " + (isLong ? "buy" : isShort ? "sell" : "neutral");

  if (isLong) {
    simpleHeroIcon.textContent = "🟢";
    simpleHeroAction.textContent = "BUY NOW (LONG)";
    simpleHeroSub.textContent = "Market is rising. AI detects upward momentum with favorable odds.";
  } else if (isShort) {
    simpleHeroIcon.textContent = "🔴";
    simpleHeroAction.textContent = "SELL NOW (SHORT)";
    simpleHeroSub.textContent = "Market is falling. AI detects downward pressure. Be cautious holding.";
  } else {
    simpleHeroIcon.textContent = "⚪";
    simpleHeroAction.textContent = "WAIT & RELAX";
    simpleHeroSub.textContent = "Market is moving sideways. Wait for a clear move before risking capital.";
  }

  // Squeeze Risk Badge in Simple Mode
  if (simpleRiskBadge) {
    const sq = Math.round((laya?.squeeze_risk || 0.1) * 100);
    if (sq > 60) {
      simpleRiskBadge.className = "risk-badge high";
      simpleRiskBadge.textContent = "⚠️ High Whipsaw Risk";
    } else if (sq > 30) {
      simpleRiskBadge.className = "risk-badge medium";
      simpleRiskBadge.textContent = "⚡ Moderate Risk";
    } else {
      simpleRiskBadge.className = "risk-badge low";
      simpleRiskBadge.textContent = "🛡️ Safe Zone";
    }
  }

  // 2. Win Chance Meter
  const conf = Math.round(signal.confidence_pct || 50);
  const confRating = conf >= 70 ? "High Win Chance" : conf >= 50 ? "Good Opportunity" : "Moderate Chance";
  simpleConfidenceText.textContent = `${confRating} (${conf}%)`;
  simpleMeterBar.style.width = `${Math.min(100, Math.max(15, conf))}%`;
  simpleMeterBar.style.background = isLong
    ? "linear-gradient(90deg, #3b82f6, #0ecb81)"
    : isShort
    ? "linear-gradient(90deg, #f97316, #f6465d)"
    : "linear-gradient(90deg, #64748b, #94a3b8)";

  // 3. 3-Step Plan
  simpleEntryPrice.textContent = "$" + formatPrice(signal.entry_price);
  simpleTakeProfit.textContent = "$" + formatPrice(signal.take_profit_1);
  simpleStopLoss.textContent = "$" + formatPrice(signal.stop_loss);

  const bePrice = signal.break_even || (signal.targets && signal.targets.break_even);
  if (simpleBreakEven) {
    simpleBreakEven.textContent = bePrice ? "$" + formatPrice(bePrice) : "--";
  }

  // Gain & Loss percentage calculations
  const entry = Number(signal.entry_price);
  const tp = Number(signal.take_profit_1);
  const sl = Number(signal.stop_loss);
  const be = Number(bePrice);

  if (entry > 0 && tp > 0 && sl > 0) {
    const gainPct = isLong ? (((tp - entry) / entry) * 100).toFixed(2) : (((entry - tp) / entry) * 100).toFixed(2);
    const lossPct = isLong ? (((entry - sl) / entry) * 100).toFixed(2) : (((sl - entry) / entry) * 100).toFixed(2);
    simpleTpDesc.textContent = `Sell here to lock in +${gainPct}% profit!`;
    simpleSlDesc.textContent = `Safety stop to cap max loss at -${lossPct}%`;
    if (simpleBeDesc && be > 0) {
      const bePct = isLong ? (((be - entry) / entry) * 100).toFixed(2) : (((entry - be) / entry) * 100).toFixed(2);
      simpleBeDesc.textContent = `At +${bePct}% (+1.0R), move Stop Loss to Entry ($${formatPrice(entry)}) to eliminate risk!`;
    }

    // Visual Risk/Reward bar
    if (rrGainLabel && rrRiskLabel && rrGainFill && rrRiskFill) {
      rrGainLabel.textContent = `Target Win: +${gainPct}%`;
      rrRiskLabel.textContent = `Safety SL: -${lossPct}%`;
      const ratio = Number(gainPct) / (Number(gainPct) + Number(lossPct) || 1);
      rrGainFill.style.width = `${Math.round(ratio * 100)}%`;
      rrRiskFill.style.width = `${100 - Math.round(ratio * 100)}%`;
    }

    // Update Position Demo Calculator
    updateDemoCalculator(Number(gainPct), Number(lossPct));
  }

  // 4. Plain English Reasons
  simpleReasonsList.innerHTML = "";
  const reasons = [];
  reasons.push(`🤖 <strong>Laya AI Recommendation:</strong> Calibrated setup with <strong>${conf}%</strong> confidence.`);

  if (latest) {
    if (latest.rsi < 35) {
      reasons.push("🏷️ <strong>Bargain Discount:</strong> Price reached an oversold floor, inviting buyers.");
    } else if (latest.rsi > 65) {
      reasons.push("🚀 <strong>Hot Momentum:</strong> Strong buying surge in progress.");
    } else {
      reasons.push("⚖️ <strong>Balanced Flow:</strong> Price action is stable without overextension.");
    }

    if (isLong) {
      reasons.push("📈 <strong>Upward Tide:</strong> Buyers are dominant above moving averages.");
    } else if (isShort) {
      reasons.push("📉 <strong>Downward Tide:</strong> Selling pressure is dominant below key support.");
    } else {
      reasons.push("⏸️ <strong>Tight Range:</strong> Price is oscillating in consolidation.");
    }

    if (latest.vol_spike && latest.vol_spike > 1.2) {
      reasons.push(`🔥 <strong>High Activity:</strong> Volume is ${latest.vol_spike}x above average.`);
    }
  }

  reasons.forEach((r) => {
    const li = document.createElement("li");
    li.innerHTML = r;
    simpleReasonsList.appendChild(li);
  });
}

// --------------------------------------------------------
// Demo Position Return Calculator
// --------------------------------------------------------
function updateDemoCalculator(gainPct, lossPct) {
  if (!calcWinVal || !calcLossVal) return;
  const win = ((currentCalcAmount * gainPct) / 100).toFixed(2);
  const loss = ((currentCalcAmount * lossPct) / 100).toFixed(2);
  calcWinVal.textContent = `+$${win}`;
  calcLossVal.textContent = `-$${loss}`;
}

if (calcPresetGroup) {
  calcPresetGroup.addEventListener("click", (e) => {
    const btn = e.target.closest(".calc-chip");
    if (!btn) return;
    calcPresetGroup.querySelectorAll(".calc-chip").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    currentCalcAmount = Number(btn.getAttribute("data-amount")) || 500;
    if (currentSignal) {
      const entry = Number(currentSignal.entry_price);
      const tp = Number(currentSignal.take_profit_1);
      const sl = Number(currentSignal.stop_loss);
      if (entry > 0 && tp > 0 && sl > 0) {
        const isLong = currentSignal.action === "LONG";
        const gainPct = isLong ? ((tp - entry) / entry) * 100 : ((entry - tp) / entry) * 100;
        const lossPct = isLong ? ((entry - sl) / entry) * 100 : ((sl - entry) / entry) * 100;
        updateDemoCalculator(gainPct, lossPct);
      }
    }
  });
}

// --------------------------------------------------------
// Copy Trade Plan to Clipboard
// --------------------------------------------------------
if (btnCopyTradePlan) {
  btnCopyTradePlan.addEventListener("click", () => {
    if (!currentSignal || currentSignal.action === "NEUTRAL") {
      showToast("No active trade plan right now. Run analysis first.", "info");
      return;
    }
    const sig = currentSignal;
    const text = `🐸 ${sig.symbol} (${sig.interval}) AI Trade Setup:
Action: ${sig.action} (${sig.signal_type})
Entry: $${formatPrice(sig.entry_price)}
Take Profit: $${formatPrice(sig.take_profit_1)}
Stop Loss: $${formatPrice(sig.stop_loss)}
Risk/Reward: 1 : ${sig.risk_reward_ratio}
Win Confidence: ${sig.confidence_pct}%
AI Engine: Laya System-1 GPU`;

    navigator.clipboard.writeText(text).then(
      () => showToast("Trade Plan Copied to Clipboard! 📋", "success"),
      () => showToast("Could not copy to clipboard", "info")
    );
  });
}

// --------------------------------------------------------
// Mode Switching (Simple vs Pro)
// --------------------------------------------------------
function setMode(mode) {
  currentMode = mode;
  localStorage.setItem("laya_mode", mode);

  if (mode === "simple") {
    btnSimpleMode.classList.add("active");
    btnSimpleMode.setAttribute("aria-selected", "true");
    btnProMode.classList.remove("active");
    btnProMode.setAttribute("aria-selected", "false");

    simpleModeContainer.style.display = "flex";
    proModeContainer.style.display = "none";

    // 100% clean chart for beginners: HIDE ALL INDICATORS AND TARGET LINES
    ema9Series.applyOptions({ visible: false });
    ema21Series.applyOptions({ visible: false });
    ema50Series.applyOptions({ visible: false });
    bbUpperSeries.applyOptions({ visible: false });
    bbLowerSeries.applyOptions({ visible: false });
    bbMiddleSeries.applyOptions({ visible: false });
    clearPriceLines();

    if (toggleEMA) toggleEMA.checked = false;
    if (toggleBB) toggleBB.checked = false;
  } else {
    btnProMode.classList.add("active");
    btnProMode.setAttribute("aria-selected", "true");
    btnSimpleMode.classList.remove("active");
    btnSimpleMode.setAttribute("aria-selected", "false");

    proModeContainer.style.display = "flex";
    simpleModeContainer.style.display = "none";

    // In Pro Mode: Only show subtle EMA 9 and 21 by default (keep BB and EMA50 clean/off unless checked)
    ema9Series.applyOptions({ visible: true });
    ema21Series.applyOptions({ visible: true });
    ema50Series.applyOptions({ visible: false });
    bbUpperSeries.applyOptions({ visible: toggleBB?.checked || false });
    bbLowerSeries.applyOptions({ visible: toggleBB?.checked || false });
    bbMiddleSeries.applyOptions({ visible: toggleBB?.checked || false });

    if (toggleEMA) toggleEMA.checked = true;

    // Draw clean target lines in Pro mode
    if (currentSignal) {
      updateChartPriceLines(currentSignal);
    }
  }
}

btnSimpleMode.addEventListener("click", () => setMode("simple"));
btnProMode.addEventListener("click", () => setMode("pro"));

// --------------------------------------------------------
// Mobile View Tabs (< 900px)
// --------------------------------------------------------
mobileTabButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    mobileTabButtons.forEach((b) => {
      b.classList.remove("active");
      b.setAttribute("aria-pressed", "false");
    });
    btn.classList.add("active");
    btn.setAttribute("aria-pressed", "true");
    const tab = btn.getAttribute("data-tab");
    mainContent.setAttribute("data-active-tab", tab);

    if (tab === "chart" && chart) {
      setTimeout(() => chart.timeScale().fitContent(), 100);
    }
  });
});

// --------------------------------------------------------
// Real-Time WebSocket Connection
// --------------------------------------------------------
function connectWebSocket() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${window.location.host}/ws/live`;

  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    wsStatusText.textContent = "Live Feed Active";
    statusDot.className = "status-dot online pulsing";
  };

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      if (msg.type === "candle_tick") {
        const c = msg.data;
        if (c.symbol === currentSymbol) {
          candleSeries.update({
            time: c.time,
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close,
          });

          volumeSeries.update({
            time: c.time,
            value: c.volume,
            color: c.close >= c.open ? "rgba(14, 203, 129, 0.2)" : "rgba(246, 70, 93, 0.2)",
          });

          updateLivePriceDisplay(c.close);

          // Live real-time update for open position lines & HUD
          if (activeToobitPositions && activeToobitPositions.length > 0) {
            activeToobitPositions[0].markPrice = c.close;
            updateChartPositionLines(activeToobitPositions, currentPositionSuggestion);
          }

          if (c.is_closed && toggleAutoAnalyze?.checked && c.time !== lastAnalyzedCandleTime) {
            lastAnalyzedCandleTime = c.time;
            triggerAnalyze();
          }
        }
      } else if (msg.type === "new_signal") {
        applySignal(msg.signal, msg.latest, msg.laya);
        if (msg.latest) updateTelemetry(msg.latest);
      }
    } catch (e) {
      console.error("WS Parse error:", e);
    }
  };

  ws.onclose = () => {
    wsStatusText.textContent = "Reconnecting...";
    statusDot.className = "status-dot";
    setTimeout(connectWebSocket, 2000);
  };

  ws.onerror = () => {
    ws.close();
  };
}

// --------------------------------------------------------
// API Actions
// --------------------------------------------------------
let analyzeInFlight = false;

async function triggerAnalyze() {
  if (analyzeInFlight) return;
  analyzeInFlight = true;
  analyzeBtn.disabled = true;
  analyzeBtn.setAttribute("aria-busy", "true");
  const originalText = analyzeBtn.innerHTML;
  analyzeBtn.innerHTML = `<span>Analyzing...</span>`;

  try {
    const resp = await fetch(`/api/analyze?symbol=${encodeURIComponent(currentSymbol)}&interval=${currentInterval}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    applySignal(data.signal, data.latest, data.laya);
    if (data.latest) updateTelemetry(data.latest);
    showToast(`Laya Evaluated: ${data.signal.action} (${data.signal.confidence_pct}%)`, "success");
  } catch (err) {
    console.error("Analysis failed:", err);
    showToast("Analysis request failed. Please check connection.", "error");
  } finally {
    analyzeInFlight = false;
    analyzeBtn.disabled = false;
    analyzeBtn.removeAttribute("aria-busy");
    analyzeBtn.innerHTML = `<i data-lucide="sparkles"></i> <span>Analyze Market</span>`;
    lucide.createIcons();
  }
}

// --------------------------------------------------------
// Signal History Management & Drawer
// --------------------------------------------------------
function addSignalToHistory(sig) {
  // Deduplicate in local history by candle timestamp
  const existingIdx = rawSignalHistory.findIndex((s) => s.time === sig.time && s.symbol === sig.symbol && s.interval === sig.interval);
  if (existingIdx >= 0) {
    rawSignalHistory[existingIdx] = sig;
  } else {
    rawSignalHistory.unshift(sig);
  }
  if (rawSignalHistory.length > 100) rawSignalHistory.pop();
  renderHistoryTable();
}

function renderHistoryTable() {
  historyTableBody.innerHTML = "";

  const filtered = rawSignalHistory.filter((s) => {
    if (activeHistoryFilter === "long") return s.action === "LONG";
    if (activeHistoryFilter === "short") return s.action === "SHORT";
    return true;
  });

  if (filtered.length === 0) {
    historyTableBody.innerHTML = `
      <tr class="empty-row">
        <td colspan="10">No signals found matching filter. Click "Analyze Market" to evaluate.</td>
      </tr>
    `;
  } else {
    filtered.forEach((sig) => {
      const tr = document.createElement("tr");
      const isLong = sig.action === "LONG";
      const tagClass = isLong ? "tag-long" : "tag-short";

      tr.innerHTML = `
        <td>${formatTimestamp(sig.timestamp || sig.time)}</td>
        <td><strong>${sig.symbol}</strong></td>
        <td>${sig.interval}</td>
        <td><span class="${tagClass}">${sig.action}</span></td>
        <td>${formatPrice(sig.entry_price)}</td>
        <td class="sl-val">${formatPrice(sig.stop_loss)}</td>
        <td class="tp-val">${formatPrice(sig.take_profit_1)}</td>
        <td class="tp-val">${formatPrice(sig.take_profit_2)}</td>
        <td><strong>${sig.confidence_pct}%</strong></td>
        <td>${sig.latency_ms || 25} ms</td>
      `;
      historyTableBody.appendChild(tr);
    });
  }

  const count = rawSignalHistory.length;
  if (historyCount) historyCount.textContent = `${count} Signals`;
  if (mobileTabHistoryCount) mobileTabHistoryCount.textContent = count;
}

async function loadHistory() {
  try {
    const resp = await fetch("/api/history");
    if (!resp.ok) return;
    const data = await resp.json();
    rawSignalHistory = data.history || [];
    renderHistoryTable();
  } catch (e) {
    console.error("Failed loading history:", e);
  }
}

// History Drawer Collapse / Expand
function toggleHistoryDrawer() {
  const isCollapsed = historyDrawer.classList.contains("collapsed");
  if (isCollapsed) {
    historyDrawer.classList.remove("collapsed");
  } else {
    historyDrawer.classList.add("collapsed");
  }
  lucide.createIcons();
}

if (historyDrawerHeader) {
  historyDrawerHeader.addEventListener("click", (e) => {
    if (e.target.closest(".history-actions")) return;
    toggleHistoryDrawer();
  });
}

histFilterButtons.forEach((btn) => {
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    histFilterButtons.forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    activeHistoryFilter = btn.getAttribute("data-filter");
    renderHistoryTable();
  });
});

if (btnClearHistory) {
  btnClearHistory.addEventListener("click", (e) => {
    e.stopPropagation();
    rawSignalHistory = [];
    chartMarkers = [];
    candleSeries?.setMarkers([]);
    renderHistoryTable();
    showToast("History log and chart markers cleared.", "info");
  });
}

if (btnExportHistory) {
  btnExportHistory.addEventListener("click", (e) => {
    e.stopPropagation();
    if (rawSignalHistory.length === 0) {
      showToast("No history to export.", "info");
      return;
    }
    const headers = ["Time", "Symbol", "Interval", "Action", "Entry", "SL", "TP1", "TP2", "Confidence", "Latency"];
    const rows = rawSignalHistory.map((s) => [
      new Date(s.timestamp * 1000).toISOString(),
      s.symbol,
      s.interval,
      s.action,
      s.entry_price,
      s.stop_loss,
      s.take_profit_1,
      s.take_profit_2,
      s.confidence_pct + "%",
      (s.latency_ms || 25) + "ms",
    ]);

    const csvContent = "data:text/csv;charset=utf-8," + [headers.join(","), ...rows.map((r) => r.join(","))].join("\n");
    const link = document.createElement("a");
    link.setAttribute("href", encodeURI(csvContent));
    link.setAttribute("download", `laya_signals_${currentSymbol}_${Date.now()}.csv`);
    document.body.appendChild(link);
    link.click();
    link.remove();
    showToast("Exported signals to CSV!", "success");
  });
}

// --------------------------------------------------------
// Event Listeners
// --------------------------------------------------------
symbolSelect.addEventListener("change", async (e) => {
  currentSymbol = e.target.value;
  clearPriceLines();
  clearPositionLines();
  if (chartPositionBadge) chartPositionBadge.classList.add("hide");
  chartMarkers = [];
  candleSeries?.setMarkers([]);
  await fetch(`/api/switch?symbol=${encodeURIComponent(currentSymbol)}&interval=${currentInterval}`, { method: "POST" });
  await loadKlines(currentSymbol, currentInterval);
  fetchToobitPositions();
  triggerAnalyze();
});

tfButtons.forEach((btn) => {
  btn.addEventListener("click", async () => {
    tfButtons.forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    currentInterval = btn.getAttribute("data-interval");
    clearPriceLines();
    chartMarkers = [];
    candleSeries?.setMarkers([]);
    await fetch(`/api/switch?symbol=${encodeURIComponent(currentSymbol)}&interval=${currentInterval}`, { method: "POST" });
    await loadKlines(currentSymbol, currentInterval);
    triggerAnalyze();
  });
});

analyzeBtn.addEventListener("click", () => triggerAnalyze());

// Indicator Toggles
toggleEMA?.addEventListener("change", (e) => {
  const visible = e.target.checked;
  ema9Series.applyOptions({ visible });
  ema21Series.applyOptions({ visible });
});

toggleBB?.addEventListener("change", (e) => {
  const visible = e.target.checked;
  bbUpperSeries.applyOptions({ visible });
  bbLowerSeries.applyOptions({ visible });
  bbMiddleSeries.applyOptions({ visible });
});

toggleTargetLines?.addEventListener("change", () => {
  if (currentSignal) updateChartPriceLines(currentSignal);
});

// Chart Controls: Auto-Fit & Fullscreen
btnFitChart?.addEventListener("click", () => {
  if (chart) {
    chart.timeScale().fitContent();
    showToast("Chart Auto-Fitted", "info");
  }
});

btnFullscreenChart?.addEventListener("click", () => {
  const chartSec = document.getElementById("chartSectionView");
  if (!chartSec) return;
  if (!document.fullscreenElement) {
    chartSec.requestFullscreen?.().catch(() => chartSec.classList.toggle("fullscreen-active"));
  } else {
    document.exitFullscreen?.();
  }
});

document.addEventListener("fullscreenchange", () => {
  const icon = document.getElementById("fullscreenIcon");
  if (document.fullscreenElement) {
    icon?.setAttribute("data-lucide", "minimize-2");
  } else {
    icon?.setAttribute("data-lucide", "expand");
  }
  lucide.createIcons();
  if (chart) chart.timeScale().fitContent();
});

// Audio Alerts Toggle
btnAudioToggle?.addEventListener("click", () => {
  audioEnabled = !audioEnabled;
  btnAudioToggle.classList.toggle("active", audioEnabled);
  audioIcon?.setAttribute("data-lucide", audioEnabled ? "bell" : "bell-off");
  lucide.createIcons();
  showToast(audioEnabled ? "Audio alerts enabled 🔔" : "Audio alerts muted 🔕", "info");
});

// Keyboard Shortcuts
document.addEventListener("keydown", (e) => {
  if (["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName)) return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;

  const modalOpen = toobitModal && !toobitModal.classList.contains("hide");

  // While the modal is open only Escape is handled (below)
  if (modalOpen && e.key !== "Escape") return;

  if (e.code === "Space") {
    e.preventDefault();
    triggerAnalyze();
  } else if (e.key === "s" || e.key === "S") {
    setMode(currentMode === "simple" ? "pro" : "simple");
    showToast(`Switched to ${currentMode.toUpperCase()} Mode`, "info");
  } else if (e.key === "f" || e.key === "F") {
    btnFullscreenChart?.click();
  } else if (["1", "2", "3", "4", "5"].includes(e.key)) {
    const idx = parseInt(e.key) - 1;
    if (tfButtons[idx]) tfButtons[idx].click();
  } else if (e.key === "Escape") {
    closeToobitModal();
  }
});

// Auto-analyze interval timer
function setupAutoAnalyze() {
  if (autoAnalyzeTimer) clearInterval(autoAnalyzeTimer);
  autoAnalyzeTimer = setInterval(() => {
    if (document.hidden) return; // don't burn requests on a hidden tab
    load24hTicker(currentSymbol);
    if (toggleAutoAnalyze?.checked) {
      triggerAnalyze();
    }
  }, 20000); // Every 20 seconds
}

// ========================================================
// TOOBIT ACCOUNT & POSITION MANAGEMENT CONTROLLER
// ========================================================

let lastFocusedBeforeModal = null;

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

function openToobitModal() {
  if (!toobitModal) return;
  hideToobitConnectError();
  lastFocusedBeforeModal = document.activeElement;
  toobitModal.classList.remove("hide");
  toobitModal.style.display = "flex";
  fetchToobitStatus();
  setTimeout(() => btnCloseToobitModal?.focus(), 50);
}

function closeToobitModal() {
  if (!toobitModal || toobitModal.classList.contains("hide")) return;
  hideToobitConnectError();
  toobitModal.classList.add("hide");
  toobitModal.style.display = "none";
  if (lastFocusedBeforeModal && typeof lastFocusedBeforeModal.focus === "function") {
    lastFocusedBeforeModal.focus();
  }
  lastFocusedBeforeModal = null;
}

// Keep keyboard focus inside the open modal
toobitModal?.addEventListener("keydown", (e) => {
  if (e.key !== "Tab") return;
  const focusables = Array.from(
    toobitModal.querySelectorAll("button, input, select, textarea, a[href]")
  ).filter((el) => !el.disabled && el.offsetParent !== null);
  if (focusables.length === 0) return;

  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault();
    first.focus();
  }
});

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
        modalAvailableBal.textContent = `$${Number(data.balance?.availableBalance || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})} USDT`;
      } else if (data.mode === "demo") {
        modalStatusText.textContent = "Simulated Demo Mode Active";
        if (modalStatusSub) modalStatusSub.textContent = "Trading with virtual $5,000 USDT";
        modalAvailableBal.textContent = `$${Number(data.balance?.availableBalance || 5000).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})} USDT`;
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

// Fetch Positions and AI Suggestions
async function fetchToobitPositions() {
  try {
    const [posRes, sugRes] = await Promise.all([
      fetch(`/api/toobit/positions?symbol=${encodeURIComponent(currentSymbol)}`),
      fetch(`/api/toobit/suggestions?symbol=${encodeURIComponent(currentSymbol)}`)
    ]);

    let positions = [];
    if (posRes.ok) {
      const posData = await posRes.json();
      positions = posData.positions || [];
      activeToobitPositions = positions;
    }

    let suggestionData = null;
    if (sugRes.ok) {
      suggestionData = await sugRes.json();
      currentPositionSuggestion = suggestionData.suggestion;
    }

    renderToobitPositions(positions, suggestionData?.suggestion);
  } catch (e) {
    console.warn("Failed to fetch Toobit positions/suggestions:", e);
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
  const mark = Number(pos.markPrice) || Number(lastKnownPrice) || entry;
  const liq = Number(pos.liquidationPrice) || 0;
  const qty = Number(pos.quantity) || 0;
  const margin = Number(pos.margin) || 0;
  const leverage = Number(pos.leverage) || 0;
  const pnl = Number(pos.unrealizedPnl) || 0;
  const pnlPct = Number(pos.pnlPercentage) || 0;
  const profitable = pnl >= 0;
  const money = (v) => `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

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
  set("symbol", pos.displaySymbol || pos.symbol || currentSymbol);
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

  // PnL hero
  const pnlAbs = Math.abs(pnl).toFixed(2);
  const pctAbs = Math.abs(pnlPct).toFixed(1);
  setPnl("pnl", "pos-hero-pnl", pnlAbs === "0.00" ? "$0.00" : `${pnl < 0 ? "-" : "+"}$${pnlAbs}`);
  setPnl("pnlPct", "pos-hero-pct", pctAbs === "0.0" ? "0.0%" : `${pnlPct < 0 ? "-" : "+"}${pctAbs}%`);

  // Stats
  set("qty", qty.toLocaleString());
  set("notional", money(qty * mark));
  set("margin", money(margin));
  set("lev", leverage > 0 ? `${leverage}x` : "--");
  set("entryStat", entry > 0 ? `$${formatPrice(entry)}` : "--");
  set("mark", mark > 0 ? `$${formatPrice(mark)}` : "--");
  set("entry", entry > 0 ? `$${formatPrice(entry)}` : "--");
  set("liq", liq > 0 ? `$${formatPrice(liq)}` : "--");

  // Liquidation distance bar: liq sits at 0%, twice the entry distance at 100% (break-even at 50%)
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
      // Colour tracks the price's position on the track, not the raw distance
      // (a fresh 10x position always starts ~9% from liquidation)
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
        proSugSl.textContent = suggestion.recommendedSl > 0 ? `$${suggestion.recommendedSl.toFixed(6)}` : "--";
      }
      if (proSugTp) {
        proSugTp.textContent = suggestion.recommendedTp > 0 ? `$${suggestion.recommendedTp.toFixed(6)}` : "--";
      }
      if (proSugText) {
        proSugText.textContent = suggestion.suggestion || "Monitoring live order book and tick flow...";
      }
    }
  }

  // --- CHART POSITION LINES & FLOATING HUD ---
  updateChartPositionLines(positions, suggestion);
}

// Flash Close Execution
async function executeFlashClose() {
  try {
    const symbolToClose = currentSymbol;
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
      clearPositionLines();
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
        symbol: currentSymbol,
        side: side,
        quantity: 100000,
        entryPrice: lastKnownPrice || 0.00418,
        leverage: 10
      })
    });
    const data = await res.json();
    if (data.success) {
      showToast(`Simulated ${side} 10x position opened for ${currentSymbol}!`, "bull");
      playSignalChime(side === "LONG");
    }
    await fetchToobitPositions();
  } catch (e) {
    showToast(`Failed to simulate position: ${e.message}`, "error");
  }
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
    const res = await fetch("/api/toobit/connect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        apiKey: apiKey,
        apiSecret: apiSecret,
        isDemo: false
      })
    });
    const data = await res.json();

    if (data.success) {
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
    const res = await fetch("/api/toobit/connect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ isDemo: true })
    });
    const data = await res.json();
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
function initToobitManager() {
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
    updateChartPositionLines(activeToobitPositions, currentPositionSuggestion);
  });

  // Simulation Listeners
  btnSimpleSimLong?.addEventListener("click", () => simulateDemoTrade("LONG"));
  btnSimpleSimShort?.addEventListener("click", () => simulateDemoTrade("SHORT"));
  btnProSimLong?.addEventListener("click", () => simulateDemoTrade("LONG"));
  btnProSimShort?.addEventListener("click", () => simulateDemoTrade("SHORT"));

  // Start periodic polling for positions (every 8 seconds to prevent API flooding)
  fetchToobitStatus();
  fetchToobitPositions();
  if (toobitPollInterval) clearInterval(toobitPollInterval);
  toobitPollInterval = setInterval(() => {
    if (document.hidden) return;
    fetchToobitPositions();
  }, 8000);
}

// --------------------------------------------------------
// Bootstrap Application
// --------------------------------------------------------
window.addEventListener("DOMContentLoaded", async () => {
  initChart();
  await loadKlines(currentSymbol, currentInterval);
  connectWebSocket();
  await loadHistory();
  setupAutoAnalyze();
  initToobitManager();
  setMode(currentMode);
  lucide.createIcons();
  setTimeout(triggerAnalyze, 800);
});

