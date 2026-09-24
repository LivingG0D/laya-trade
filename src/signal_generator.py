from typing import Dict, Any, List, Optional
import time

from .config import DEFAULT_ATR_SL_MULTIPLIER, DEFAULT_TP1_RR, DEFAULT_TP2_RR

class SignalGenerator:
    def __init__(
        self,
        atr_multiplier: float = DEFAULT_ATR_SL_MULTIPLIER,
        tp1_rr: float = DEFAULT_TP1_RR,
        tp2_rr: float = DEFAULT_TP2_RR
    ):
        self.atr_multiplier = atr_multiplier
        self.tp1_rr = tp1_rr
        self.tp2_rr = tp2_rr
        self.signal_history: List[Dict[str, Any]] = []

    def generate_signal(
        self,
        market_state: Dict[str, Any],
        laya_decision: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Merge market indicators with Laya decision to produce institutional grade trade setups.
        Applies strict confluence gating, break-even targets, and volatility adjustments.
        """
        cur_price = market_state.get("price", 0.0)
        symbol = str(market_state.get("symbol", "BTCUSDT.P")).upper()
        interval = market_state.get("interval", "5m")
        candle_time = market_state.get("time", int(time.time()))

        atr = market_state.get("atr", cur_price * 0.01)
        atr_pct = market_state.get("atr_pct", 1.0)
        support = market_state.get("support", cur_price - atr)
        resistance = market_state.get("resistance", cur_price + atr)

        # High beta / meme coin volatility adjustment (prevent stop-hunts on PEPE, DOGE, SHIB, WIF, BONK, etc.)
        meme_keywords = ["PEPE", "SHIB", "DOGE", "FLOKI", "BONK", "WIF", "POPCAT", "NEIRO", "TURBO", "CAT"]
        is_meme_or_high_vol = any(k in symbol for k in meme_keywords) or atr_pct > 1.5
        effective_atr_mult = self.atr_multiplier * 1.35 if is_meme_or_high_vol else self.atr_multiplier

        pos = laya_decision.get("position", "NEUTRAL_HOLD")
        probs = laya_decision.get("probabilities", {})

        is_long = "LONG" in pos
        is_short = "SHORT" in pos

        # Extract macro context and institutional metrics
        htf = market_state.get("htf") or {}
        htf_trend = htf.get("trend", "NEUTRAL")
        depth = market_state.get("depth") or {}
        obi = depth.get("imbalance_pct", 0.0)
        funding = market_state.get("funding") or {}
        market_regime = market_state.get("market_regime", "CHOP_RANGE")
        is_squeeze = market_state.get("is_squeeze", False)

        dir_prob = laya_decision.get("long_prob" if is_long else "short_prob")
        raw_prob = dir_prob if dir_prob is not None else probs.get(pos, 0.5)

        # ========================================================
        # STRICT CONFLUENCE GATEKEEPER (INSTITUTIONAL QUALITY FILTER)
        # ========================================================
        filter_reason = None

        # 1. Reject counter-trend trades against strong 1H macro trend
        if is_long and htf_trend == "STRONG_BEARISH":
            filter_reason = "Long trade rejected: Opposed to 1H Strong Bearish Macro Trend (confluence failure)."
        elif is_short and htf_trend == "STRONG_BULLISH":
            filter_reason = "Short trade rejected: Opposed to 1H Strong Bullish Macro Trend (confluence failure)."

        # 2. Strict Confidence Threshold Gate (Minimum 65% conviction required)
        elif (is_long or is_short) and raw_prob < 0.65:
            filter_reason = f"Trade rejected: Model conviction ({round(raw_prob * 100, 1)}%) below institutional threshold (65.0%)."

        # 3. Chop Market Filter (Avoid whipsaws when ADX is dead unless in pre-squeeze compression)
        elif (is_long or is_short) and market_regime == "CHOP_RANGE" and not is_squeeze and raw_prob < 0.72:
            filter_reason = "Trade filtered: Low ADX range chop without squeeze expansion catalyst."

        if filter_reason:
            is_long = False
            is_short = False
            pos = "NEUTRAL_HOLD"

        # Precision formatting
        def fmt(val: float) -> float:
            return round(val, 8 if cur_price < 0.1 else 4)

        reasons = []

        if is_long:
            action = "LONG"
            direction = "BUY"
            color = "#0ecb81"
            shape = "arrowUp"
            marker_pos = "belowBar"
            dir_prob = raw_prob
            
            # SL placed below entry by adjusted ATR or key structural support
            sl_dist = max(atr * effective_atr_mult, cur_price * 0.006)
            if 0 < (cur_price - support) <= sl_dist * 1.5:
                stop_loss = support - (atr * 0.25)
            else:
                stop_loss = cur_price - sl_dist

            risk = cur_price - stop_loss
            be_price = cur_price + (risk * 1.0) # +1.0R Break-Even
            tp1 = cur_price + (risk * self.tp1_rr) # +1.5R (Take 50% profit)
            tp2 = cur_price + (risk * self.tp2_rr) # +2.5R (Runner target)

            reasons.append(f"Laya Decision: {pos} ({round(dir_prob * 100, 1)}% probability)")
            if htf_trend != "NEUTRAL":
                reasons.append(f"1H Macro Alignment: {htf_trend} (Trend Confluence Verified)")
            if obi != 0:
                reasons.append(f"Order Flow Depth: OBI {obi:+.1f}% ({'Bids' if obi > 0 else 'Asks'} dominate Toobit book)")
            if is_squeeze:
                reasons.append("TTM Squeeze Active: Volatility compression pending explosive expansion.")
            reasons.append(f"Execution Rule: Move SL to Break-Even (${fmt(be_price)}) once +1.0R is reached.")
            reasons.append(f"Take Profit Strategy: Scale out 50% at TP1 (${fmt(tp1)}); trail runner to TP2 (${fmt(tp2)}).")

        elif is_short:
            action = "SHORT"
            direction = "SELL"
            color = "#f6465d"
            shape = "arrowDown"
            marker_pos = "aboveBar"
            dir_prob = raw_prob

            # SL placed above entry by adjusted ATR or key structural resistance
            sl_dist = max(atr * effective_atr_mult, cur_price * 0.006)
            if 0 < (resistance - cur_price) <= sl_dist * 1.5:
                stop_loss = resistance + (atr * 0.25)
            else:
                stop_loss = cur_price + sl_dist

            risk = stop_loss - cur_price
            be_price = cur_price - (risk * 1.0) # +1.0R Break-Even
            tp1 = cur_price - (risk * self.tp1_rr) # +1.5R (Take 50% profit)
            tp2 = cur_price - (risk * self.tp2_rr) # +2.5R (Runner target)

            reasons.append(f"Laya Decision: {pos} ({round(dir_prob * 100, 1)}% probability)")
            if htf_trend != "NEUTRAL":
                reasons.append(f"1H Macro Alignment: {htf_trend} (Trend Confluence Verified)")
            if obi != 0:
                reasons.append(f"Order Flow Depth: OBI {obi:+.1f}% ({'Bids' if obi > 0 else 'Asks'} dominate Toobit book)")
            if is_squeeze:
                reasons.append("TTM Squeeze Active: Volatility compression pending explosive expansion.")
            reasons.append(f"Execution Rule: Move SL to Break-Even (${fmt(be_price)}) once +1.0R is reached.")
            reasons.append(f"Take Profit Strategy: Scale out 50% at TP1 (${fmt(tp1)}); trail runner to TP2 (${fmt(tp2)}).")

        else:
            action = "NEUTRAL"
            direction = "HOLD"
            color = "#848e9c"
            shape = "circle"
            marker_pos = "aboveBar"
            dir_prob = probs.get("NEUTRAL_HOLD", laya_decision.get("hold_prob", 0.5))
            stop_loss = cur_price
            be_price = cur_price
            tp1 = cur_price
            tp2 = cur_price
            risk = 0.0

            if filter_reason:
                reasons.append(f"Quality Filter: {filter_reason}")
            else:
                reasons.append("Market in chop/range or conflicting timeframes. Preserving capital.")
            if htf_trend != "NEUTRAL":
                reasons.append(f"1H Macro Trend: {htf_trend}")
            if market_regime:
                reasons.append(f"Market Regime: {market_regime}")

        # Build signal setup object
        signal_obj = {
            "id": f"sig_{int(time.time() * 1000)}",
            "time": candle_time,
            "timestamp": int(time.time()),
            "symbol": symbol,
            "interval": interval,
            "action": action,
            "direction": direction,
            "signal_type": pos,
            "entry_price": fmt(cur_price),
            "stop_loss": fmt(stop_loss),
            "break_even": fmt(be_price),
            "take_profit_1": fmt(tp1),
            "take_profit_2": fmt(tp2),
            "targets": {
                "entry": fmt(cur_price),
                "stop_loss": fmt(stop_loss),
                "break_even": fmt(be_price),
                "take_profit_1": fmt(tp1),
                "take_profit_2": fmt(tp2)
            },
            "risk_reward_ratio": round(self.tp1_rr, 1),
            "confidence_pct": round(dir_prob * 100, 1),
            "confidence_score": laya_decision.get("confidence_score", 2),
            "squeeze_risk": laya_decision.get("squeeze_risk", 0.0),
            "time_horizon": laya_decision.get("time_horizon", "SCALP"),
            "latency_ms": laya_decision.get("latency_ms", 0.0),
            "engine": laya_decision.get("engine", "laya"),
            "reasons": reasons,
            "marker": {
                "time": candle_time,
                "position": marker_pos,
                "color": color,
                "shape": shape,
                "text": "BUY" if action == "LONG" else "SELL",
                "size": 1
            } if action != "NEUTRAL" else None
        }

        # Save to history (deduplicate by candle time so only 1 signal per candle bar)
        if action != "NEUTRAL":
            sym = signal_obj["symbol"]
            intv = signal_obj["interval"]
            existing_idx = next(
                (i for i, s in enumerate(self.signal_history)
                 if s.get("time") == candle_time and s.get("symbol") == sym and s.get("interval") == intv),
                None
            )
            if existing_idx is not None:
                self.signal_history[existing_idx] = signal_obj
            else:
                self.signal_history.insert(0, signal_obj)
            if len(self.signal_history) > 50:
                self.signal_history = self.signal_history[:50]

        return signal_obj
