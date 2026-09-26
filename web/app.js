// ========================================================
// LAYA CRYPTO TRADINGVIEW CHART ANALYZER — APP.JS (CLEAN & DEDUPLICATED)
// ========================================================

import { formatPrice, formatCompact, formatTimestamp, formatUsd } from "./js/format.js";
import { showToast, playSignalChime, toggleAudioEnabled } from "./js/feedback.js";
import {
  initToobitManager,
  fetchToobitPositions,
  handlePositionTick,
} from "./js/toobit.js";
import { initCoinSearch } from "./js/coin_search.js";
import {
  initAutoTradeManager,
  handleAutoTradeWsMessage
} from "./js/autotrade.js";

let chart = null;
let coinSearchManager = null;
let candleSeries = null;
let volumeSeries = null;
let ema9Series = null;
let ema21Series = null;
let ema50Series = null;
let bbUpperSeries = null;
let bbLowerSeries = null;
let bbMiddleSeries = null;

let currentSymbol = "BTCUSDT.P";
let currentInterval = "5m";
let ws = null;
let autoAnalyzeTimer = null;
let activePriceLines = [];
let chartMarkers = [];
let lastAnalyzedCandleTime = 0;
let lastKnownPrice = null;
let currentSignal = null;
let currentCalcAmount = 500;
let rawSignalHistory = [];
let activeHistoryFilter = "all";
let activeKlineRequestId = 0;
let lastCandleBarTime = 0;

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
const chartLoading = document.getElementById("chartLoading");

// On-Chart Floating Position HUD Elements
const chartPositionBadge = document.getElementById("chartPositionBadge");
const posHudSide = document.getElementById("posHudSide");
const posHudSymbol = document.getElementById("posHudSymbol");
const posHudEntry = document.getElementById("posHudEntry");
const posHudPnl = document.getElementById("posHudPnl");
const posHudLiq = document.getElementById("posHudLiq");

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
const targetEntrySub = document.getElementById("targetEntrySub");
const targetSL = document.getElementById("targetSL");
const targetSLRoe = document.getElementById("targetSLRoe");
const targetBE = document.getElementById("targetBE");
const targetBERoe = document.getElementById("targetBERoe");
const targetTP1 = document.getElementById("targetTP1");
const targetTP1Roe = document.getElementById("targetTP1Roe");
const targetTP2 = document.getElementById("targetTP2");
const targetTP2Roe = document.getElementById("targetTP2Roe");
const targetLiq = document.getElementById("targetLiq");
const targetLiqDist = document.getElementById("targetLiqDist");
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
const historyTableBody = document.getElementById("historyTableBody");
const historyCount = document.getElementById("historyCount");
const btnExportHistory = document.getElementById("btnExportHistory");
const btnClearHistory = document.getElementById("btnClearHistory");
const histFilterButtons = document.querySelectorAll(".hist-filter-btn");

// Mobile Elements
const mainContent = document.getElementById("mainContent");
const mobileTabButtons = document.querySelectorAll(".mobile-tab-btn");
const mobileTabSignalPill = document.getElementById("mobileTabSignalPill");
const mobileTabHistoryCount = document.getElementById("mobileTabHistoryCount");

// (Toasts + audio chimes live in ./js/feedback.js; the Toobit
//  account & position controller lives in ./js/toobit.js)

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
  const requestId = ++activeKlineRequestId;
  if (chartLoading) chartLoading.classList.remove("hide");

  // Clear any existing retry timeout
  if (window._klineRetryTimeout) {
    clearTimeout(window._klineRetryTimeout);
    window._klineRetryTimeout = null;
  }

  try {
    if (hudSymbol) hudSymbol.textContent = symbol;
    if (hudInterval) hudInterval.textContent = interval;

    const resp = await fetch(`/api/klines?symbol=${encodeURIComponent(symbol)}&interval=${interval}&limit=350`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();

    // Stale response guard: discard if user switched symbol or interval during the fetch
    if (requestId !== activeKlineRequestId || symbol !== currentSymbol || interval !== currentInterval) {
      return;
    }

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

    // Track the latest bar timestamp to prevent out-of-order WebSocket ticks from crashing TV charts
    if (candles.length > 0) {
      lastCandleBarTime = candles[candles.length - 1].time;
    }

    // Apply markers from history (Deduplicated & Cleaned)
    if (data.markers && data.markers.length > 0) {
      applyDeduplicatedMarkers(data.markers);
    } else {
      chartMarkers = [];
      if (candleSeries) candleSeries.setMarkers([]);
    }

    // Update Telemetry & HUD
    if (data.latest) {
      updateTelemetry(data.latest);
      updateLivePriceDisplay(data.latest.price, data.latest.change_pct);
    }

    if (chart) {
      chart.timeScale().fitContent();
    }
    load24hTicker(symbol);
  } catch (err) {
    if (requestId !== activeKlineRequestId || symbol !== currentSymbol || interval !== currentInterval) {
      return;
    }
    console.error("Error loading klines:", err);
    showToast("Reconnecting to market data stream...", "warning");
    if (!window._klineRetryTimeout) {
      window._klineRetryTimeout = setTimeout(() => {
        window._klineRetryTimeout = null;
        if (requestId === activeKlineRequestId && symbol === currentSymbol && interval === currentInterval) {
          loadKlines(symbol, interval);
        }
      }, 2500);
    }
  } finally {
    if (requestId === activeKlineRequestId && chartLoading) {
      chartLoading.classList.add("hide");
    }
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

  // 50x Leverage & ROE Target Displays
  const lev = signal.leverage || 50;
  if (targetEntrySub) targetEntrySub.textContent = `${lev}x Leverage`;
  if (targetLiq) {
    targetLiq.textContent = signal.liquidation_price ? formatPrice(signal.liquidation_price) : "--";
  }
  if (targetLiqDist) {
    const curP = Number(signal.entry_price);
    const liqP = Number(signal.liquidation_price);
    if (curP > 0 && liqP > 0) {
      const d = (Math.abs(curP - liqP) / curP) * 100;
      targetLiqDist.textContent = `Liq: ${d.toFixed(2)}% away`;
    } else {
      targetLiqDist.textContent = "Safe below SL";
    }
  }
  if (signal.roe_targets) {
    if (targetSLRoe) targetSLRoe.textContent = `${signal.roe_targets.stop_loss_roe} ROE`;
    if (targetBERoe) targetBERoe.textContent = `${signal.roe_targets.break_even_roe} ROE`;
    if (targetTP1Roe) targetTP1Roe.textContent = `${signal.roe_targets.take_profit_1_roe} ROE`;
    if (targetTP2Roe) targetTP2Roe.textContent = `${signal.roe_targets.take_profit_2_roe} ROE`;
  } else {
    if (targetSLRoe) targetSLRoe.textContent = "Max -40.0% ROE";
    if (targetBERoe) targetBERoe.textContent = "Shield to Entry";
    if (targetTP1Roe) targetTP1Roe.textContent = "+16% - +25% ROE";
    if (targetTP2Roe) targetTP2Roe.textContent = "+28% - +35% ROE";
  }

  // Anti-Volatility-Drag Position Sizing
  const targetRecMarginEl = document.getElementById("targetRecMargin");
  const targetSizingRuleEl = document.getElementById("targetSizingRule");
  if (targetRecMarginEl) {
    const recM = signal.recommended_margin_usd || 10.0;
    targetRecMarginEl.textContent = `${formatUsd(recM)} Fixed`;
  }
  if (targetSizingRuleEl && signal.sizing_rule) {
    targetSizingRuleEl.textContent = signal.sizing_rule;
  }

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
  const leverage = Number(activePos.leverage) || 50;
  const margin = Number(activePos.margin) || (leverage > 0 ? (qty * entry) / leverage : 0);

  // Real-time PnL calculation
  let pnl = Number(activePos.unrealizedPnl);
  if (isNaN(pnl) || (pnl === 0 && mark !== entry)) {
    pnl = isLong ? (mark - entry) * qty : (entry - mark) * qty;
  }
  const pnlPct = margin > 0 ? (pnl / margin) * 100 : 0;
  const pnlSign = pnl >= 0 ? "+" : "-";
  const pctAbs = Math.abs(pnlPct).toFixed(1);
  const distPct = mark > 0 && liq > 0 ? (Math.abs(mark - liq) / mark) * 100 : 0;

  // 1. Position Entry Price Line (Solid, prominent)
  const posColor = isLong ? "#10b981" : "#ef4444";
  const entryTitle = `POS ${isLong ? "LONG" : "SHORT"} ${leverage ? leverage + "x" : ""} (${formatUsd(pnl, { signed: true })} / ${pnlSign}${pctAbs}%)`;

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

  // 3. Position Active or Recommended SL / TP lines
  const effectiveTp = Number(activePos.takeProfit || activePos.takeProfit1) || (suggestion?.recommendedTp ? Number(suggestion.recommendedTp) : 0);
  if (effectiveTp > 0) {
    try {
      const tpLine = candleSeries.createPriceLine({
        price: effectiveTp,
        color: "rgba(16, 185, 129, 0.85)",
        lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: true,
        title: `POS TP @ $${formatPrice(effectiveTp)}`,
      });
      activePositionLines.push(tpLine);
    } catch (e) {}
  }

  const effectiveSl = Number(activePos.stopLoss) || (suggestion?.recommendedSl ? Number(suggestion.recommendedSl) : 0);
  if (effectiveSl > 0) {
    try {
      const slLine = candleSeries.createPriceLine({
        price: effectiveSl,
        color: "rgba(239, 68, 68, 0.85)",
        lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: true,
        title: `POS SL @ $${formatPrice(effectiveSl)}`,
      });
      activePositionLines.push(slLine);
    } catch (e) {}
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
      posHudPnl.textContent = `${formatUsd(pnl, { signed: true })} (${pnlSign}${pctAbs}%)`;
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
    const leverage = signal.leverage || 50;
    const gainPct = isLong ? ((tp - entry) / entry) * 100 : ((entry - tp) / entry) * 100;
    const lossPct = isLong ? ((entry - sl) / entry) * 100 : ((sl - entry) / entry) * 100;
    const gainRoe = (gainPct * leverage).toFixed(1);
    const lossRoe = (lossPct * leverage).toFixed(1);

    simpleTpDesc.textContent = `Lock in +${gainRoe}% ROE profit (+${gainPct.toFixed(2)}% price move)!`;
    simpleSlDesc.textContent = `Safety stop caps loss at -${lossRoe}% ROE (-${lossPct.toFixed(2)}% price move, safe from liq)`;
    if (simpleBeDesc && be > 0) {
      const bePct = isLong ? ((be - entry) / entry) * 100 : ((entry - be) / entry) * 100;
      const beRoe = (bePct * leverage).toFixed(1);
      simpleBeDesc.textContent = `At +${beRoe}% ROE (+1.0R / $${formatPrice(be)}), move SL to Entry for a 100% Risk-Free trade!`;
    }

    // Visual Risk/Reward bar
    if (rrGainLabel && rrRiskLabel && rrGainFill && rrRiskFill) {
      rrGainLabel.textContent = `Target Win: +${gainRoe}% ROE`;
      rrRiskLabel.textContent = `Safety SL: -${lossRoe}% ROE`;
      const ratio = Number(gainPct) / (Number(gainPct) + Number(lossPct) || 1);
      rrGainFill.style.width = `${Math.round(ratio * 100)}%`;
      rrRiskFill.style.width = `${100 - Math.round(ratio * 100)}%`;
    }

    // Update Position Demo Calculator with 50x ROE
    updateDemoCalculator(Number(gainRoe), Number(lossRoe));
  }

  // 4. Plain English Reasons
  simpleReasonsList.innerHTML = "";
  const reasons = [];
  reasons.push(`🤖 <strong>Laya AI Recommendation:</strong> Calibrated 50x setup with <strong>${conf}%</strong> confidence.`);

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
// Demo Position Return Calculator (50x Leverage Margin)
// --------------------------------------------------------
function updateDemoCalculator(gainRoe, lossRoe) {
  if (!calcWinVal || !calcLossVal) return;
  const win = ((currentCalcAmount * Number(gainRoe)) / 100).toFixed(2);
  const loss = ((currentCalcAmount * Number(lossRoe)) / 100).toFixed(2);
  calcWinVal.textContent = `+$${win} (+${gainRoe}%)`;
  calcLossVal.textContent = `-$${loss} (-${lossRoe}%)`;
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
      const leverage = currentSignal.leverage || 50;
      if (entry > 0 && tp > 0 && sl > 0) {
        const isLong = currentSignal.action === "LONG";
        const gainPct = isLong ? ((tp - entry) / entry) * 100 : ((entry - tp) / entry) * 100;
        const lossPct = isLong ? ((entry - sl) / entry) * 100 : ((sl - entry) / entry) * 100;
        const gainRoe = (gainPct * leverage).toFixed(1);
        const lossRoe = (lossPct * leverage).toFixed(1);
        updateDemoCalculator(Number(gainRoe), Number(lossRoe));
      }
    }
  });
}

// --------------------------------------------------------
// Copy Trade Plan to Clipboard (50x Futures Setup)
// --------------------------------------------------------
if (btnCopyTradePlan) {
  btnCopyTradePlan.addEventListener("click", () => {
    if (!currentSignal || currentSignal.action === "NEUTRAL") {
      showToast("No active trade plan right now. Run analysis first.", "info");
      return;
    }
    const sig = currentSignal;
    const roe = sig.roe_targets || {};
    const text = `⚡ ${sig.symbol} (${sig.interval}) AI 50x Futures Setup:
Action: ${sig.action} (${sig.signal_type})
Leverage: 50x Perpetual Futures
Entry: $${formatPrice(sig.entry_price)}
Take Profit 1: $${formatPrice(sig.take_profit_1)} (${roe.take_profit_1_roe || "+60.0% ROE"})
Take Profit 2: $${formatPrice(sig.take_profit_2)} (${roe.take_profit_2_roe || "+100.0% ROE"})
Break-Even Trigger: $${formatPrice(sig.break_even || sig.entry_price)} (${roe.break_even_roe || "+32.0% ROE"})
Stop Loss: $${formatPrice(sig.stop_loss)} (${roe.stop_loss_roe || "-32.0% ROE"})
Est. Liquidation: $${formatPrice(sig.liquidation_price || "0")} (Safe beyond SL)
Position Sizing: Fixed ${formatUsd(sig.recommended_margin_usd || 10)} Margin (Max 20% Kelly Anti-Drag Shield)
Risk/Reward: 1 : ${sig.risk_reward_ratio}
Win Confidence: ${sig.confidence_pct}%
AI Engine: Laya System-1 GPU (fine_tuned_laya_crypto)`;

    navigator.clipboard.writeText(text).then(
      () => showToast("50x Trade Plan Copied to Clipboard! 📋", "success"),
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

// The server runs ONE shared upstream stream and fans it out to every client,
// so a stream left on another symbol/interval (another tab, a previous session)
// silently starves this page's chart. Clients claim the stream on load and
// re-claim it if matching ticks stop arriving.
const STREAM_REASSERT_COOLDOWN_MS = 20000;
let lastStreamReassertAt = 0;

async function claimStream(reason = "manual") {
  const url = `/api/switch?symbol=${encodeURIComponent(currentSymbol)}&interval=${encodeURIComponent(currentInterval)}`;
  try {
    await fetch(url, { method: "POST" });
    lastStreamReassertAt = Date.now();
    if (reason !== "silent") {
      console.info(`[stream] claimed ${currentSymbol} @ ${currentInterval} (${reason})`);
    }
  } catch (err) {
    console.warn("/api/switch failed:", err);
  }
}

function connectWebSocket() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${window.location.host}/ws/live`;

  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    wsStatusText.textContent = "Live Feed Active";
    statusDot.className = "status-dot online pulsing";
    // Claim the shared stream for this page's symbol/timeframe on every connect.
    claimStream("connect");
  };

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      if (msg.type === "candle_tick") {
        const c = msg.data;
        if (c.symbol === currentSymbol) {
          // Record that this page's symbol is being fed, so the watchdog below
          // can distinguish "quiet market" from "someone took the stream".
          window.__lastMatchingTickAt = Date.now();
          window.__lastTickInterval = c.interval;

          // Real-time price display & active position tracking updates regardless of timeframe
          updateLivePriceDisplay(c.close);
          handlePositionTick(c.close);

          // ONLY update candlestick and volume series if the tick matches active interval
          if (!c.interval || c.interval === currentInterval) {
            // Guard: Never feed a tick older than the last bar to TradingView Lightweight Charts
            if (lastCandleBarTime > 0 && c.time < lastCandleBarTime) {
              return;
            }

            try {
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

              lastCandleBarTime = Math.max(lastCandleBarTime, c.time);
            } catch (updateErr) {
              console.warn("Out-of-order candle tick update safely dropped:", updateErr);
            }

            if (c.is_closed && toggleAutoAnalyze?.checked && c.time !== lastAnalyzedCandleTime) {
              lastAnalyzedCandleTime = c.time;
              triggerAnalyze();
            }
          }
        }
      } else if (msg.type === "new_signal") {
        const sig = msg.signal;
        if (sig && sig.action !== "NEUTRAL") {
          addSignalToHistory(sig);
        }
        if (sig && sig.symbol === currentSymbol && sig.interval === currentInterval) {
          applySignal(msg.signal, msg.latest, msg.laya);
          if (msg.latest) updateTelemetry(msg.latest);
        }
      } else if (msg.type === "autotrade_log" || msg.type === "autotrade_update") {
        handleAutoTradeWsMessage(msg);
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
  analyzeBtn.innerHTML = `<span>Analyzing...</span>`;

  const requestedSymbol = currentSymbol;
  const requestedInterval = currentInterval;

  try {
    const resp = await fetch(`/api/analyze?symbol=${encodeURIComponent(requestedSymbol)}&interval=${requestedInterval}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();

    // Stale guard: if user switched symbol or interval during analysis, do not overwrite dashboard/chart
    if (requestedSymbol !== currentSymbol || requestedInterval !== currentInterval) {
      return;
    }

    applySignal(data.signal, data.latest, data.laya);
    if (data.latest) updateTelemetry(data.latest);
    showToast(`Laya Evaluated: ${data.signal.action} (${data.signal.confidence_pct}%)`, "success");
  } catch (err) {
    if (requestedSymbol === currentSymbol && requestedInterval === currentInterval) {
      console.error("Analysis failed:", err);
      showToast("Analysis request failed. Please check connection.", "error");
    }
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
      new Date((s.timestamp || s.time || 0) > 1e11 ? (s.timestamp || s.time) : (s.timestamp || s.time || 0) * 1000).toISOString(),
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
  const newSymbol = e.target.value;
  if (newSymbol === currentSymbol) return;

  currentSymbol = newSymbol;
  coinSearchManager?.updateActiveCoinDisplay(currentSymbol);
  lastCandleBarTime = 0;
  lastAnalyzedCandleTime = 0;

  if (window._klineRetryTimeout) {
    clearTimeout(window._klineRetryTimeout);
    window._klineRetryTimeout = null;
  }

  clearPriceLines();
  clearPositionLines();
  if (chartPositionBadge) chartPositionBadge.classList.add("hide");
  chartMarkers = [];
  candleSeries?.setMarkers([]);

  claimStream("symbol-change");

  await loadKlines(currentSymbol, currentInterval);
  fetchToobitPositions();
  triggerAnalyze();
});

tfButtons.forEach((btn) => {
  btn.addEventListener("click", async () => {
    const newInterval = btn.getAttribute("data-interval");
    if (newInterval === currentInterval) return;

    tfButtons.forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    currentInterval = newInterval;
    lastCandleBarTime = 0;
    lastAnalyzedCandleTime = 0;

    if (window._klineRetryTimeout) {
      clearTimeout(window._klineRetryTimeout);
      window._klineRetryTimeout = null;
    }

    clearPriceLines();
    chartMarkers = [];
    candleSeries?.setMarkers([]);

    // Fire stream resubscribe in background without blocking chart rendering
    claimStream("timeframe-change");

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
  const enabled = toggleAudioEnabled();
  btnAudioToggle.classList.toggle("active", enabled);
  audioIcon?.setAttribute("data-lucide", enabled ? "bell" : "bell-off");
  lucide.createIcons();
  showToast(enabled ? "Audio alerts enabled 🔔" : "Audio alerts muted 🔕", "info");
});

// Keyboard Shortcuts
document.addEventListener("keydown", (e) => {
  if (["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName)) return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;

  // Open <dialog>s handle their own Escape; shortcuts stay off until they close
  if (document.querySelector("dialog[open]")) return;

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

// --------------------------------------------------------
// Bootstrap Application
// --------------------------------------------------------
window.addEventListener("DOMContentLoaded", async () => {
  coinSearchManager = initCoinSearch({
    getActiveSymbol: () => currentSymbol,
  });
  initChart();
  await loadKlines(currentSymbol, currentInterval);
  connectWebSocket();
  await loadHistory();
  setupAutoAnalyze();
  initToobitManager({
    getSymbol: () => currentSymbol,
    getFallbackPrice: () => lastKnownPrice,
    updateChartPositionLines,
    clearPositionLines,
    switchSymbol: (sym) => {
      const select = document.getElementById("symbolSelect");
      if (select && sym) {
        const normSym = sym.toUpperCase().replace(/[-_.]/g, "").replace("SWAP", "").replace("P", "");
        let targetVal = null;
        for (const opt of select.options) {
          const normOpt = opt.value.toUpperCase().replace(/[-_.]/g, "").replace("SWAP", "").replace("P", "");
          if (normOpt === normSym) {
            targetVal = opt.value;
            break;
          }
        }
        if (targetVal) {
          select.value = targetVal;
          select.dispatchEvent(new Event("change"));
          showToast(`Switched chart to ${targetVal} 📈`, "info");
        }
      }
    },
  });
  initAutoTradeManager({
    getSymbol: () => currentSymbol,
    fetchToobitPositions,
  });
  setMode(currentMode);
  lucide.createIcons();
  setTimeout(triggerAnalyze, 800);

  // Watchdog: the upstream stream is shared across all clients, so if another
  // tab/window takes it this page's candles quietly stop moving even though the
  // page still looks healthy. Re-claim the stream when matching ticks dry up.
  window.__lastMatchingTickAt = Date.now();
  setInterval(() => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const since = Date.now() - (window.__lastMatchingTickAt || Date.now());
    const wrongInterval = window.__lastTickInterval && window.__lastTickInterval !== currentInterval;
    const stale = since > 20000;
    if (!stale && !wrongInterval) return;
    if (Date.now() - lastStreamReassertAt < STREAM_REASSERT_COOLDOWN_MS) return;
    console.warn(
      `[stream] no matching ticks for ${Math.round(since / 1000)}s ` +
      `(feed=${window.__lastTickInterval || "none"}, want=${currentInterval}) — re-claiming`
    );
    claimStream("watchdog");
  }, 8000);
});

