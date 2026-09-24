// ========================================================
// Price / number formatting helpers (pure, no DOM access)
// ========================================================

export function formatPrice(val) {
  if (val === null || val === undefined || isNaN(val)) return "--";
  if (val < 0.01) {
    // 7 decimals is what the chart uses; trim trailing zeros for readability
    return Number(val).toFixed(7).replace(/0+$/, "").replace(/\.$/, "");
  }
  if (val < 1.0) return Number(val).toFixed(4);
  return Number(val).toFixed(2);
}

export function formatCompact(val) {
  if (!val) return "0";
  if (val >= 1e9) return (val / 1e9).toFixed(2) + "B";
  if (val >= 1e6) return (val / 1e6).toFixed(2) + "M";
  if (val >= 1e3) return (val / 1e3).toFixed(2) + "K";
  return Number(val).toFixed(0);
}

export function formatTimestamp(ts) {
  if (!ts) return "--";
  const ms = ts > 1e11 ? ts : ts * 1000;
  const d = new Date(ms);
  return isNaN(d.getTime()) ? "--" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
