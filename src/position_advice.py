"""Position advice: plain-language suggestions for the dashboard's position card.

Display only: nothing here places or changes an order. It reads the latest
signal and decision and returns the card's headline, steps and suggested
stop/target prices.
"""

import math
from typing import Any, Dict, Optional

from .config import DEFAULT_KELLY_FRACTION_PCT, DEFAULT_POSITION_MARGIN_USD


def _probability_value(probabilities: Any, *keys: str, default: float = 0.0) -> float:
    """Read a probability consistently from legacy lowercase or current uppercase keys."""
    if not isinstance(probabilities, dict):
        return default
    for key in keys:
        value = probabilities.get(key)
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric):
            return min(1.0, max(0.0, numeric))
    return default


def _confidence_percent(value: Any = None, score: Any = None) -> float:
    """Normalize model confidence to a 0-100 display percentage."""
    candidate = value
    if candidate is None:
        candidate = score
        try:
            numeric_score = float(candidate)
            if 1.0 <= numeric_score <= 4.0:
                candidate = numeric_score / 4.0
        except (TypeError, ValueError):
            return 50.0
    try:
        numeric = float(candidate)
    except (TypeError, ValueError):
        return 50.0
    if not math.isfinite(numeric):
        return 50.0
    if 0.0 <= numeric <= 1.0:
        numeric *= 100.0
    return min(100.0, max(0.0, numeric))


def generate_position_suggestion(
    position: Optional[Dict[str, Any]],
    current_price: float,
    signal: Optional[Dict[str, Any]] = None,
    laya_decision: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Synthesize position status with Laya System-1 probabilistic intelligence
    to produce actionable trade management recommendations.
    """
    sig = signal or {}
    laya = laya_decision or {}
    action = str(sig.get("action") or laya.get("position") or "NEUTRAL").upper()
    raw_confidence = sig.get("confidence_pct")
    if raw_confidence is None:
        raw_confidence = sig.get("confidence")
    if raw_confidence is None:
        raw_confidence = laya.get("confidence_prob", laya.get("confidence"))
    confidence = _confidence_percent(raw_confidence, laya.get("confidence_score"))
    probs = laya.get("probabilities", {})
    # The signal's own prices; missing keys fall back to the defaults at each use.
    targets = {key: sig[key] for key in ("entry_price", "stop_loss", "take_profit_1") if sig.get(key)}

    p_strong_long = _probability_value(probs, "STRONG_LONG", "strong_long", default=0.2)
    p_scalp_long = _probability_value(probs, "SCALP_LONG", "scalp_long", default=0.2)
    p_bull = p_strong_long + p_scalp_long

    p_strong_short = _probability_value(probs, "STRONG_SHORT", "strong_short", default=0.2)
    p_scalp_short = _probability_value(probs, "SCALP_SHORT", "scalp_short", default=0.2)
    p_bear = p_strong_short + p_scalp_short

    # Helper formatter to prevent showing $0.000000
    def fmt_p(val: float) -> str:
        if not val or val <= 0:
            return "--"
        return f"${val:.6f}" if val < 0.1 else f"${val:.2f}"

    # Determine baseline price
    base_price = current_price
    if base_price <= 0:
        base_price = sig.get("entry_price") or 0.0

    def card(has_position, alignment, action_code, badge, badge_class, headline, text, steps, sl, tp):
        return {
            "hasPosition": has_position, "alignment": alignment, "action": action_code,
            "badgeText": badge, "badgeClass": badge_class, "headline": headline,
            "suggestion": text, "steps": steps, "canFlashClose": has_position,
            "recommendedSl": sl, "recommendedTp": tp,
        }

    # No active position: suggest an entry setup (LONG checked first, as before)
    if not position:
        if "LONG" not in action and "SHORT" not in action:
            return card(
                False, "NONE", "WAIT", "WAIT FOR SETUP ⚪", "badge-neutral",
                "Market Consolidating: Stand By",
                "No active position. Market is currently consolidating without high directional conviction. Wait for Laya to identify a high-probability breakout.",
                ["Keep powder dry and avoid over-trading",
                 "Wait for next candle close to confirm direction",
                 "Watch key support and resistance boundaries"],
                0.0, 0.0,
            )
        long = "LONG" in action
        entry = targets.get("entry_price", base_price)
        if entry <= 0:
            entry = base_price
        tp_mult, sl_mult = (1.015, 0.985) if long else (0.985, 1.015)
        tp1 = targets.get("take_profit_1", entry * tp_mult if entry > 0 else 0.0)
        sl = targets.get("stop_loss", entry * sl_mult if entry > 0 else 0.0)
        side = "LONG" if long else "SHORT"
        margin = f"${DEFAULT_POSITION_MARGIN_USD:.2f}"
        return card(
            False, "NONE", "ENTER_SETUP",
            "BULLISH SETUP 🟢" if long else "BEARISH SETUP 🔴",
            "badge-profit" if long else "badge-danger",
            f"Ready for Entry: Laya suggests {action}",
            f"No active position. Laya System-1 detects {'bullish confluence' if long else 'downward momentum'} ({confidence}% confidence). "
            f"Recommended setup: {'Buy' if long else 'Short'} near {fmt_p(entry)}, Target TP1 at {fmt_p(tp1)}, Protective SL at {fmt_p(sl)}. "
            f"Use fixed {margin} margin to prevent compounding volatility drag.",
            [f"Consider opening {side} near {fmt_p(entry)}",
             f"Position Sizing: Use Fixed {margin} margin (or max {int(DEFAULT_KELLY_FRACTION_PCT*100)}% equity shield)",
             f"Set Target Take Profit 1 at {fmt_p(tp1)}",
             f"Set Stop Loss at {fmt_p(sl)}"],
            sl, tp1,
        )

    # Position is active: judge it against the side the model now favours
    long = position.get("side", "LONG").upper() == "LONG"
    side, against = ("LONG", "SHORT") if long else ("SHORT", "LONG")
    entry = position.get("entryPrice", current_price)
    pnl = position.get("unrealizedPnl", 0.0)
    pnl_pct = position.get("pnlPercentage", 0.0)
    symbol = position.get("displaySymbol", position.get("symbol", "contract"))
    p_with, p_against = (p_bull, p_bear) if long else (p_bear, p_bull)
    tp_mult, sl_mult, tp_far = (1.015, 0.985, 1.02) if long else (0.985, 1.015, 0.98)
    sl_target = targets.get("stop_loss", entry * sl_mult)
    liquidation = f"Liquidation threshold is at ${position.get('liquidationPrice', 0.0):.6f}"

    if p_with >= 0.50 or side in action:
        if pnl_pct >= 8.0:
            rec_sl = max(entry, current_price * 0.992) if long else min(entry, current_price * 1.008)
            return card(
                True, "ALIGNED", "HOLD_TRAIL_SL", "PROTECT PROFIT 🛡️", "badge-profit",
                f"Great Trade (+{pnl_pct:.1f}%): Trail Stop Loss" if long
                else f"Short Working Well (+{pnl_pct:.1f}%): Trail Stop Loss",
                f"Your {symbol} LONG is up +{pnl_pct:.1f}% (${pnl:+.2f}). Laya AI confirms ongoing bullish pressure ({int(p_bull*100)}%). Recommend moving your Stop Loss to break-even or above entry (${rec_sl:.6f}) to lock in guaranteed profit while riding the trend." if long
                else f"Your {symbol} SHORT is profitable (+{pnl_pct:.1f}% / ${pnl:+.2f}). Laya AI shows continued bearish follow-through ({int(p_bear*100)}%). Lower your Stop Loss to ${rec_sl:.6f} to lock in profit.",
                [f"Move Stop Loss to ${rec_sl:.6f} (Risk-Free Mode)" if long else f"Lower Stop Loss to ${rec_sl:.6f} (Risk-Free)",
                 f"Next Take Profit target: ${targets.get('take_profit_1', current_price * tp_mult):.6f}",
                 "Let winners run while protecting capital" if long else "Let downward trend continue"],
                rec_sl, targets.get("take_profit_1", current_price * tp_far),
            )
        return card(
            True, "ALIGNED", "HOLD_TRAIL_SL",
            "KEEP HOLDING 🟢" if long else "KEEP HOLDING 🔴", "badge-profit",
            "Bullish Trend Aligned: Hold Position" if long else "Bearish Trend Aligned: Hold Short",
            (f"Laya AI probabilities favor continued upward move ({int(p_bull*100)}% bullish). Keep holding your LONG position" if long
             else f"Laya AI indicates strong seller dominance ({int(p_bear*100)}% bearish). Keep holding your SHORT position")
            + f" towards target ${targets.get('take_profit_1', entry * tp_mult):.6f}.",
            ["Keep position open" if long else "Keep SHORT position open",
             f"Ensure Stop Loss is active at ${sl_target:.6f}",
             f"Watch for TP1 target near ${targets.get('take_profit_1', entry * tp_far):.6f}"],
            sl_target, targets.get("take_profit_1", entry * tp_far),
        )

    if p_against >= 0.50 or against in action:
        if pnl > 0:
            return card(
                True, "OPPOSED", "TAKE_PROFIT", "TAKE PROFIT 🎯", "badge-warning",
                "⚠️ Momentum Turning Bearish: Cash Out Now" if long else "⚠️ Bullish Bounce Detected: Close Short",
                f"Warning: Laya AI detects strong downward shift ({int(p_bear*100)}% short pressure). Your LONG is currently profitable (+{pnl_pct:.1f}%). Strongly consider taking profit or clicking Flash Close before momentum reverses your gains." if long
                else f"Warning: Buyers are stepping in ({int(p_bull*100)}% bull probability). Your SHORT is in profit (+{pnl_pct:.1f}%). Cash out now using Flash Close to secure your gains.",
                ["Click 'Flash Close' to capture profits immediately",
                 "Or close 50% size and tighten SL to current price",
                 "Prepare for potential short entry if breakdown confirms"] if long else
                ["Click 'Flash Close' to capture profits",
                 "Avoid holding short into strong bounce",
                 "Look for clean re-entry or wait"],
                current_price * (0.998 if long else 1.002), current_price,
            )
        return card(
            True, "OPPOSED", "CUT_LOSS",
            "EXIT / REDUCE RISK 🚨" if long else "EXIT / CUT LOSS 🚨", "badge-danger",
            "🚨 Bearish Breakdown: Reduce Exposure" if long else "🚨 Bullish Squeeze Risk: Exit Short",
            f"Risk Alert: Price is declining ({pnl_pct:.1f}%) and Laya AI indicates sellers are in control ({int(p_bear*100)}% bearish). Exit position via Flash Close to avoid deeper drawdown or liquidation." if long
            else f"Risk Alert: Price is moving up against your SHORT ({pnl_pct:.1f}%). Laya AI signals bullish momentum ({int(p_bull*100)}%). Flash close now to prevent short-squeeze liquidation.",
            ["Click 'Flash Close' to limit loss", liquidation, "Preserve margin for higher-conviction setup"] if long
            else ["Click 'Flash Close' immediately", liquidation, "Protect account equity"],
            current_price, entry,
        )

    return card(
        True, "NEUTRAL", "HOLD_TRAIL_SL", "MONITOR ⏳", "badge-neutral",
        "Market Pausing: Maintain Guard" if long else "Market Sideways: Keep Alert",
        f"Price is in a consolidation range. Your LONG is at {pnl_pct:+.1f}%. Keep your protective Stop Loss at ${sl_target:.6f} and wait for volume to pick up." if long
        else f"Price is stalling. Your SHORT is at {pnl_pct:+.1f}%. Keep your protective Stop Loss at ${sl_target:.6f}.",
        ["Hold position while price remains above support" if long else "Hold position while price stays below resistance",
         f"Stop loss safety boundary: ${sl_target:.6f}",
         "Monitor for next trend impulse" if long else "Wait for seller volume"],
        sl_target, targets.get("take_profit_1", entry * tp_mult),
    )
