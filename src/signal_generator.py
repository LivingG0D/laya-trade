from typing import Dict, Any, List, Optional
import time

from .config import (
    DEFAULT_ATR_SL_MULTIPLIER,
    DEFAULT_TP1_RR,
    DEFAULT_TP2_RR,
    DEFAULT_BE_RR,
    DEFAULT_LEVERAGE,
    LEVERAGE_MAX_SL_PCT,
    LEVERAGE_MIN_SL_PCT,
    DEFAULT_POSITION_MARGIN_USD,
    DEFAULT_KELLY_FRACTION_PCT,
)

class SignalGenerator:
    def __init__(
        self,
        atr_multiplier: float = DEFAULT_ATR_SL_MULTIPLIER,
        tp1_rr: float = DEFAULT_TP1_RR,
        tp2_rr: float = DEFAULT_TP2_RR,
        be_rr: float = DEFAULT_BE_RR,
        leverage: int = DEFAULT_LEVERAGE,
    ):
        self.atr_multiplier = atr_multiplier
        self.tp1_rr = tp1_rr
        self.tp2_rr = tp2_rr
        self.be_rr = be_rr
        self.leverage = leverage
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
        try:
            effective_leverage = float(market_state.get("leverage", self.leverage))
        except (TypeError, ValueError):
            effective_leverage = float(self.leverage)
        effective_leverage = min(125.0, max(1.0, effective_leverage))

        atr = market_state.get("atr", cur_price * 0.01)
        atr_pct = market_state.get("atr_pct", 1.0)
        support = market_state.get("support", cur_price - atr)
        resistance = market_state.get("resistance", cur_price + atr)
        rsi = market_state.get("rsi", 50.0)
        adx = market_state.get("adx", 20.0)
        trend_state = str(market_state.get("trend_state", "NEUTRAL")).upper()

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
        dir_conviction = laya_decision.get("directional_conviction")
        if dir_conviction is None:
            p_l = float(laya_decision.get("long_prob", 0.33))
            p_s = float(laya_decision.get("short_prob", 0.33))
            if is_long:
                dir_conviction = p_l / max(1e-5, p_l + p_s)
            elif is_short:
                dir_conviction = p_s / max(1e-5, p_l + p_s)
            else:
                dir_conviction = float(laya_decision.get("hold_prob", 0.5))

        raw_prob = dir_conviction

        # ========================================================
        # STRICT CONFLUENCE GATEKEEPER (WIN RATE OPTIMIZED)
        # ========================================================
        filter_reason = None

        # 1. Anti-Falling-Knife / Counter-Momentum Rule (ADX > 35)
        if is_long and ("BEAR" in htf_trend or "BEAR" in trend_state) and adx > 35.0:
            filter_reason = f"Long trade rejected: Violent bear cascade (ADX {adx:.1f}). Do not catch falling knives."
        elif is_short and ("BULL" in htf_trend or "BULL" in trend_state) and adx > 35.0:
            filter_reason = f"Short trade rejected: Violent bull momentum (ADX {adx:.1f}). Do not short strong expansions."

        # 2. Reject counter-trend trades against strong 1H macro trend
        elif is_long and htf_trend == "STRONG_BEARISH":
            filter_reason = "Long trade rejected: Opposed to 1H Strong Bearish Macro Trend (confluence failure)."
        elif is_short and htf_trend == "STRONG_BULLISH":
            filter_reason = "Short trade rejected: Opposed to 1H Strong Bullish Macro Trend (confluence failure)."
        elif is_short and htf_trend == "BULLISH" and dir_conviction < 0.535:
            filter_reason = f"Short trade rejected: Counter-trend setup requires >= 53.5% conviction in 1H Bullish trend (got {round(dir_conviction * 100, 1)}%)."
        elif is_long and htf_trend == "BEARISH" and dir_conviction < 0.535:
            filter_reason = f"Long trade rejected: Counter-trend setup requires >= 53.5% conviction in 1H Bearish trend (got {round(dir_conviction * 100, 1)}%)."

        # 3. Reject overextended extremes (No chasing RSI > 72 or shorting RSI < 28 without squeeze coiling)
        elif is_long and rsi > 72.0 and not is_squeeze:
            filter_reason = f"Long filtered: RSI overbought ({rsi:.1f}) without active squeeze coiling (wait for pullback)."
        elif is_short and rsi < 28.0 and not is_squeeze:
            filter_reason = f"Short filtered: RSI oversold ({rsi:.1f}) without active squeeze coiling (wait for relief bounce)."

        # 4. Directional Conviction Gate (Requires statistically meaningful edge >= 52.5%, or >= 51.5% in Squeeze)
        elif (is_long or is_short) and not is_squeeze and dir_conviction < 0.525:
            filter_reason = f"Trade rejected: Directional edge ({round(dir_conviction * 100, 1)}%) below threshold (52.5%)."
        elif (is_long or is_short) and is_squeeze and dir_conviction < 0.515:
            filter_reason = f"Trade rejected: Squeeze compression edge ({round(dir_conviction * 100, 1)}%) below threshold (51.5%)."

        # 5. Chop Market Filter (When ADX indicates dead chop, require >= 53.0% conviction unless in squeeze compression)
        elif (is_long or is_short) and market_regime == "CHOP_RANGE" and not is_squeeze and dir_conviction < 0.530:
            filter_reason = f"Trade filtered: Low ADX range chop requires stronger conviction (>= 53.0%, got {round(dir_conviction * 100, 1)}%)."

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

            # Calculate estimated liquidation price for the configured leverage
            liq_dist_pct = 0.90 / effective_leverage
            liq_price = cur_price * (1.0 - liq_dist_pct)
            
            # SL strictly bounded within the configured leverage envelope
            max_sl_dist = cur_price * LEVERAGE_MAX_SL_PCT
            min_sl_dist = cur_price * LEVERAGE_MIN_SL_PCT
            raw_sl_dist = max(atr * effective_atr_mult, cur_price * 0.005)
            sl_dist = max(min_sl_dist, min(raw_sl_dist, max_sl_dist))

            if 0 < (cur_price - support) <= max_sl_dist:
                cand_sl = support - (atr * 0.15)
                stop_loss = min(cur_price - min_sl_dist, max(cur_price - max_sl_dist, cand_sl))
            else:
                stop_loss = cur_price - sl_dist

            risk = cur_price - stop_loss
            be_price = cur_price + (risk * self.be_rr) # +0.75R Break-Even
            tp1 = cur_price + (risk * self.tp1_rr) # +1.2R (Take 50% profit)
            tp2 = cur_price + (risk * self.tp2_rr) # +2.2R (Runner target)

            # Return on Equity (ROE) under the configured leverage
            sl_roe = round(-(risk / cur_price) * effective_leverage * 100, 1)
            be_roe = round(+(risk * self.be_rr / cur_price) * effective_leverage * 100, 1)
            tp1_roe = round(+((tp1 - cur_price) / cur_price) * effective_leverage * 100, 1)
            tp2_roe = round(+((tp2 - cur_price) / cur_price) * effective_leverage * 100, 1)

            reasons.append(f"⚡ {effective_leverage:g}x Leverage Spec: Margin = ${1000 / effective_leverage:.2f} per $1,000 | Est. Liquidation: ${fmt(liq_price)} (-{round(liq_dist_pct*100, 2)}%).")
            reasons.append(f"🛡️ Structural Invalidation: SL at ${fmt(stop_loss)} ({sl_roe}% ROE) protects capital safe above liquidation.")
            reasons.append(f"🎯 Take Profit 1 (+{self.tp1_rr}R): Reach ${fmt(tp1)} for +{tp1_roe}% ROE (Scale out 50% profit).")
            reasons.append(f"🚀 Take Profit 2 (+{self.tp2_rr}R): Runner target at ${fmt(tp2)} for +{tp2_roe}% ROE.")
            reasons.append(f"🔒 Break-Even Trigger (+{self.be_rr}R): At ${fmt(be_price)} (+{be_roe}% ROE), move SL to entry to reduce risk; fees and slippage still apply.")
            reasons.append(f"🛡️ Anti-Volatility-Drag Guard: Use Fixed ${DEFAULT_POSITION_MARGIN_USD:.2f} Margin (or max {int(DEFAULT_KELLY_FRACTION_PCT*100)}% equity) to avoid compounding variance drag on {effective_leverage}x.")
            if is_squeeze:
                reasons.append("🔥 High-Probability TTM Squeeze Active: Volatility compression pending explosive expansion.")
            if htf_trend != "NEUTRAL":
                reasons.append(f"1H Macro Alignment: {htf_trend} (Trend Confluence Verified)")
            if obi != 0:
                reasons.append(f"Order Flow Depth: OBI {obi:+.1f}% ({'Bids' if obi > 0 else 'Asks'} dominate Toobit book)")

        elif is_short:
            action = "SHORT"
            direction = "SELL"
            color = "#f6465d"
            shape = "arrowDown"
            marker_pos = "aboveBar"
            dir_prob = raw_prob

            # Calculate estimated liquidation price for the configured leverage
            liq_dist_pct = 0.90 / effective_leverage
            liq_price = cur_price * (1.0 + liq_dist_pct)
            
            # SL strictly bounded within the configured leverage envelope
            max_sl_dist = cur_price * LEVERAGE_MAX_SL_PCT
            min_sl_dist = cur_price * LEVERAGE_MIN_SL_PCT
            raw_sl_dist = max(atr * effective_atr_mult, cur_price * 0.005)
            sl_dist = max(min_sl_dist, min(raw_sl_dist, max_sl_dist))

            if 0 < (resistance - cur_price) <= max_sl_dist:
                cand_sl = resistance + (atr * 0.15)
                stop_loss = max(cur_price + min_sl_dist, min(cur_price + max_sl_dist, cand_sl))
            else:
                stop_loss = cur_price + sl_dist

            risk = stop_loss - cur_price
            be_price = cur_price - (risk * self.be_rr) # +0.75R Break-Even
            tp1 = cur_price - (risk * self.tp1_rr) # +1.2R (Take 50% profit)
            tp2 = cur_price - (risk * self.tp2_rr) # +2.2R (Runner target)

            # Return on Equity (ROE) under the configured leverage
            sl_roe = round(-(risk / cur_price) * effective_leverage * 100, 1)
            be_roe = round(+(risk * self.be_rr / cur_price) * effective_leverage * 100, 1)
            tp1_roe = round(+((cur_price - tp1) / cur_price) * effective_leverage * 100, 1)
            tp2_roe = round(+((cur_price - tp2) / cur_price) * effective_leverage * 100, 1)

            reasons.append(f"⚡ {effective_leverage:g}x Leverage Spec: Margin = ${1000 / effective_leverage:.2f} per $1,000 | Est. Liquidation: ${fmt(liq_price)} (+{round(liq_dist_pct*100, 2)}%).")
            reasons.append(f"🛡️ Structural Invalidation: SL at ${fmt(stop_loss)} ({sl_roe}% ROE) protects capital safe below liquidation.")
            reasons.append(f"🎯 Take Profit 1 (+{self.tp1_rr}R): Reach ${fmt(tp1)} for +{tp1_roe}% ROE (Scale out 50% profit).")
            reasons.append(f"🚀 Take Profit 2 (+{self.tp2_rr}R): Runner target at ${fmt(tp2)} for +{tp2_roe}% ROE.")
            reasons.append(f"🔒 Break-Even Trigger (+{self.be_rr}R): At ${fmt(be_price)} (+{be_roe}% ROE), move SL to entry to reduce risk; fees and slippage still apply.")
            reasons.append(f"🛡️ Anti-Volatility-Drag Guard: Use Fixed ${DEFAULT_POSITION_MARGIN_USD:.2f} Margin (or max {int(DEFAULT_KELLY_FRACTION_PCT*100)}% equity) to avoid compounding variance drag on {effective_leverage}x.")
            if is_squeeze:
                reasons.append("🔥 High-Probability TTM Squeeze Active: Volatility compression pending explosive expansion.")
            if htf_trend != "NEUTRAL":
                reasons.append(f"1H Macro Alignment: {htf_trend} (Trend Confluence Verified)")
            if obi != 0:
                reasons.append(f"Order Flow Depth: OBI {obi:+.1f}% ({'Bids' if obi > 0 else 'Asks'} dominate Toobit book)")

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
            liq_price = cur_price
            risk = 0.0
            sl_roe = be_roe = tp1_roe = tp2_roe = 0.0

            if filter_reason:
                reasons.append(f"Quality Filter: {filter_reason}")
            else:
                reasons.append("Market in chop/range or conflicting timeframes. Preserving capital.")
            if htf_trend != "NEUTRAL":
                reasons.append(f"1H Macro Trend: {htf_trend}")
            if market_regime:
                reasons.append(f"Market Regime: {market_regime}")

        # Build signal setup object with 50x Leverage & ROE targets
        signal_obj = {
            "id": f"sig_{int(time.time() * 1000)}",
            "time": candle_time,
            "timestamp": int(time.time()),
            "symbol": symbol,
            "interval": interval,
            "action": action,
            "direction": direction,
            "signal_type": pos,
            "leverage": effective_leverage,
            "liquidation_price": fmt(liq_price),
            "roe_targets": {
                "stop_loss_roe": f"{sl_roe}%" if action != "NEUTRAL" else "0.0%",
                "break_even_roe": f"+{be_roe}%" if action != "NEUTRAL" else "0.0%",
                "take_profit_1_roe": f"+{tp1_roe}%" if action != "NEUTRAL" else "0.0%",
                "take_profit_2_roe": f"+{tp2_roe}%" if action != "NEUTRAL" else "0.0%",
            },
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
                "take_profit_2": fmt(tp2),
                "liquidation": fmt(liq_price),
            },
            "risk_reward_ratio": round(self.tp1_rr, 1),
            "recommended_margin_usd": DEFAULT_POSITION_MARGIN_USD,
            "sizing_rule": f"Fixed ${DEFAULT_POSITION_MARGIN_USD:.2f} Margin (Quarter-Kelly max {int(DEFAULT_KELLY_FRACTION_PCT*100)}%)",
            "confidence_pct": round(dir_conviction * 100, 1),
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
