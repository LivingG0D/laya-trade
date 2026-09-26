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

const USD = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const USD_SIGNED = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", signDisplay: "exceptZero" });

// "$1,234.50" / "-$3.00"; signed adds "+" to gains and shows amounts that round to zero as "$0.00".
export function formatUsd(value, { signed = false } = {}) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return (signed ? USD_SIGNED : USD).format(n);
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
