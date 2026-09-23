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
        Merge market indicators with Laya decision to produce actionable trade setups.
        """
        cur_price = market_state.get("price", 0.0)
        atr = market_state.get("atr", cur_price * 0.01)
        support = market_state.get("support", cur_price - atr)
        resistance = market_state.get("resistance", cur_price + atr)
        candle_time = market_state.get("time", int(time.time()))

        pos = laya_decision.get("position", "NEUTRAL_HOLD")
        probs = laya_decision.get("probabilities", {})

        is_long = "LONG" in pos
        is_short = "SHORT" in pos

        # Calibrated probability for direction
        if is_long:
            action = "LONG"
            direction = "BUY"
            color = "#0ecb81"
            shape = "arrowUp"
            marker_pos = "belowBar"
            dir_prob = probs.get(pos, laya_decision.get("long_prob", 0.7))
            
            # SL placed below entry by ATR or key support
            sl_dist = max(atr * self.atr_multiplier, cur_price * 0.005)
            # If support is reasonably close below entry, use support as SL anchor
            if 0 < (cur_price - support) <= sl_dist * 1.5:
                stop_loss = support - (atr * 0.2)
            else:
                stop_loss = cur_price - sl_dist

            risk = cur_price - stop_loss
            tp1 = cur_price + (risk * self.tp1_rr)
            tp2 = cur_price + (risk * self.tp2_rr)

        elif is_short:
            action = "SHORT"
            direction = "SELL"
            color = "#f6465d"
            shape = "arrowDown"
            marker_pos = "aboveBar"
            dir_prob = probs.get(pos, laya_decision.get("short_prob", 0.7))

            # SL placed above entry by ATR or key resistance
            sl_dist = max(atr * self.atr_multiplier, cur_price * 0.005)
            if 0 < (resistance - cur_price) <= sl_dist * 1.5:
                stop_loss = resistance + (atr * 0.2)
            else:
                stop_loss = cur_price + sl_dist

            risk = stop_loss - cur_price
            tp1 = cur_price - (risk * self.tp1_rr)
            tp2 = cur_price - (risk * self.tp2_rr)

        else:
            action = "NEUTRAL"
            direction = "HOLD"
            color = "#848e9c"
            shape = "circle"
            marker_pos = "aboveBar"
            dir_prob = probs.get("NEUTRAL_HOLD", laya_decision.get("hold_prob", 0.5))
            stop_loss = cur_price
            tp1 = cur_price
            tp2 = cur_price
            risk = 0.0

        # Build human-readable bullet points
        reasons = []
        reasons.append(f"Laya System 1: {pos} ({round(dir_prob * 100, 1)}% probability)")
        if market_state.get("trend_state"):
            reasons.append(f"Trend Structure: {market_state.get('trend_state')}")
        if market_state.get("rsi"):
            reasons.append(f"RSI (14): {market_state.get('rsi')}")
        if market_state.get("macd_hist") is not None:
            hist_str = "Positive/Bullish" if market_state.get("macd_hist") > 0 else "Negative/Bearish"
            reasons.append(f"MACD Histogram: {hist_str}")
        if market_state.get("vol_spike") and market_state.get("vol_spike") > 1.2:
            reasons.append(f"Volume Surge: {market_state.get('vol_spike')}x above 20-period SMA")

        # Precision formatting
        def fmt(val: float) -> float:
            return round(val, 8 if cur_price < 0.1 else 4)

        signal_obj = {
            "id": f"sig_{int(time.time() * 1000)}",
            "time": candle_time,
            "timestamp": int(time.time()),
            "symbol": market_state.get("symbol", "1000PEPEUSDT.P"),
            "interval": market_state.get("interval", "5m"),
            "action": action,
            "direction": direction,
            "signal_type": pos,
            "entry_price": fmt(cur_price),
            "stop_loss": fmt(stop_loss),
            "take_profit_1": fmt(tp1),
            "take_profit_2": fmt(tp2),
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
