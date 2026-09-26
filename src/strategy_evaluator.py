"""Deterministic cost-aware evaluation for strategy experiments.

The evaluator intentionally uses the previous candle's signal and the next
candle's open for entry. It is a research baseline, not a live execution
engine: callers must provide realistic candles and signal targets.
"""

from dataclasses import dataclass
from math import isfinite
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence

from .indicators import validate_klines


@dataclass(frozen=True)
class CostModel:
    """Per-side trading costs expressed in basis points."""

    fee_bps: float = 4.0
    slippage_bps: float = 2.0
    funding_bps_per_bar: float = 0.0

    def __post_init__(self) -> None:
        if not all(isfinite(value) and value >= 0 for value in (
            self.fee_bps,
            self.slippage_bps,
            self.funding_bps_per_bar,
        )):
            raise ValueError("Cost model values must be finite and non-negative")


@dataclass(frozen=True)
class EvaluationConfig:
    """Risk and signal thresholds for the evaluation baseline."""

    initial_equity: float = 1000.0
    risk_per_trade: float = 0.01
    max_leverage: float = 10.0
    min_confidence_pct: float = 55.0
    allow_short: bool = True
    # Live-exit mirrors, off by default. Break-even: once price moves this
    # fraction in favour (or TP1 fills), later bars use entry +/- offset as stop.
    breakeven_trigger_pct: float = 0.0
    breakeven_offset_pct: float = 0.0005
    # Close at the next open when an opposite signal reaches this confidence.
    opposing_exit_confidence: Optional[float] = None

    def __post_init__(self) -> None:
        if not isfinite(self.initial_equity) or self.initial_equity <= 0:
            raise ValueError("initial_equity must be positive")
        if not isfinite(self.risk_per_trade) or not 0 < self.risk_per_trade <= 1:
            raise ValueError("risk_per_trade must be in (0, 1]")
        if not isfinite(self.max_leverage) or self.max_leverage <= 0:
            raise ValueError("max_leverage must be positive")
        if not isfinite(self.min_confidence_pct) or not 0 <= self.min_confidence_pct <= 100:
            raise ValueError("min_confidence_pct must be in [0, 100]")
        if not all(isfinite(v) and v >= 0 for v in (self.breakeven_trigger_pct, self.breakeven_offset_pct)):
            raise ValueError("break-even settings must be finite and non-negative")


def walk_forward_windows(
    sample_count: int,
    train_size: int,
    test_size: int,
    step: Optional[int] = None,
) -> Iterator[Dict[str, int]]:
    """Yield expanding, chronological train/test windows without random leakage."""
    if sample_count <= 0 or train_size <= 0 or test_size <= 0:
        raise ValueError("sample_count, train_size, and test_size must be positive")
    step = step or test_size
    if step <= 0:
        raise ValueError("step must be positive")

    for test_start in range(train_size, sample_count, step):
        test_end = min(test_start + test_size, sample_count)
        yield {
            "train_start": 0,
            "train_end": test_start,
            "test_start": test_start,
            "test_end": test_end,
        }
        if test_end == sample_count:
            break


def _as_float(value: Any) -> Optional[float]:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if isfinite(numeric) else None


def _signal_confidence(signal: Mapping[str, Any]) -> float:
    value = _as_float(signal.get("confidence_pct"))
    if value is None:
        value = _as_float(signal.get("confidence"))
    if value is None:
        return 0.0
    if 0.0 <= value <= 1.0:
        value *= 100.0
    return min(100.0, max(0.0, value))


def _signal_side(signal: Mapping[str, Any]) -> Optional[int]:
    action = str(signal.get("action") or signal.get("position") or "").upper()
    if action in {"LONG", "BUY", "STRONG_LONG", "SCALP_LONG"}:
        return 1
    if action in {"SHORT", "SELL", "STRONG_SHORT", "SCALP_SHORT"}:
        return -1
    return None


def _signal_price(signal: Mapping[str, Any], *keys: str) -> float:
    for key in keys:
        value = _as_float(signal.get(key))
        if value is not None and value > 0:
            return value
    return 0.0


def _entry_price(open_price: float, side: int, slippage_rate: float) -> float:
    return open_price * (1.0 + slippage_rate if side == 1 else 1.0 - slippage_rate)


def _exit_price(trigger_price: float, side: int, slippage_rate: float) -> float:
    return trigger_price * (1.0 - slippage_rate if side == 1 else 1.0 + slippage_rate)


def _targets_are_valid(side: int, entry: float, stop: float, tp1: float, tp2: float) -> bool:
    if stop <= 0:
        return False
    if side == 1 and stop >= entry:
        return False
    if side == -1 and stop <= entry:
        return False
    if tp1 > 0 and ((side == 1 and tp1 <= entry) or (side == -1 and tp1 >= entry)):
        return False
    if tp2 > 0 and ((side == 1 and tp2 <= entry) or (side == -1 and tp2 >= entry)):
        return False
    if tp1 > 0 and tp2 > 0 and ((side == 1 and tp2 <= tp1) or (side == -1 and tp2 >= tp1)):
        return False
    return True


def _trigger_exit(position: Mapping[str, Any], candle: Mapping[str, Any]) -> Optional[tuple[str, float]]:
    side = position["side"]
    high = float(candle["high"])
    low = float(candle["low"])
    open_price = float(candle["open"])
    stop = position["stop"]
    tp1 = position["tp1"]
    tp2 = position["tp2"]

    if side == 1:
        if low <= stop:
            return "STOP", min(stop, open_price)  # a gap through the stop fills at the open
        if not position["tp1_hit"] and high >= tp1 > 0:
            return "TP1", tp1
        if position["tp1_hit"] and high >= tp2 > 0:
            return "TP2", tp2
    else:
        if high >= stop:
            return "STOP", max(stop, open_price)
        if not position["tp1_hit"] and low <= tp1 > 0:
            return "TP1", tp1
        if position["tp1_hit"] and low <= tp2 > 0:
            return "TP2", tp2
    return None


def _mark_equity(equity: float, position: Optional[Mapping[str, Any]], close: float) -> float:
    if position is None:
        return equity
    unrealized = position["side"] * (close - position["entry"]) * position["remaining_qty"]
    return equity + unrealized


def _close_quantity(
    position: Dict[str, Any],
    quantity: float,
    trigger_price: float,
    side: int,
    slippage_rate: float,
    fee_rate: float,
    equity: float,
) -> float:
    fill_price = _exit_price(trigger_price, side, slippage_rate)
    gross = side * (fill_price - position["entry"]) * quantity
    exit_fee = fill_price * quantity * fee_rate
    position["remaining_qty"] -= quantity
    position["realized_gross"] += gross
    position["exit_fees"] += exit_fee
    return equity + gross - exit_fee


def _move_to_breakeven(position: Dict[str, Any], offset_pct: float) -> None:
    position["stop"] = position["entry"] * (1.0 + position["side"] * offset_pct)
    position["be_moved"] = True


def _process_position(
    position: Dict[str, Any],
    candle: Mapping[str, Any],
    bar_index: int,
    equity: float,
    slippage_rate: float,
    fee_rate: float,
    funding_rate: float,
    config: EvaluationConfig,
) -> tuple[float, Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Apply funding and any conservative intrabar exit to an open position."""
    close = float(candle["close"])
    funding = position["remaining_qty"] * close * funding_rate
    position["funding"] += funding
    equity -= funding

    exit_event = _trigger_exit(position, candle)
    trade: Optional[Dict[str, Any]] = None
    breakeven_on = config.breakeven_trigger_pct > 0
    if exit_event is None:
        if breakeven_on and not position["be_moved"]:
            favourable = float(candle["high"]) if position["side"] == 1 else float(candle["low"])
            move = position["side"] * (favourable - position["entry"]) / position["entry"]
            if move >= config.breakeven_trigger_pct:
                _move_to_breakeven(position, config.breakeven_offset_pct)
        return equity, position, trade

    reason, trigger = exit_event
    if reason == "STOP" and position["be_moved"]:
        reason = "BREAKEVEN"
    if reason == "TP1" and position["tp2"] > 0:
        partial_quantity = position["remaining_qty"] / 2.0
        equity = _close_quantity(
            position,
            partial_quantity,
            trigger,
            position["side"],
            slippage_rate,
            fee_rate,
            equity,
        )
        position["tp1_hit"] = True
        if breakeven_on and not position["be_moved"]:
            _move_to_breakeven(position, config.breakeven_offset_pct)
        return equity, position, trade

    position["exit_time"] = candle.get("time")
    equity, trade = _finish_trade(
        position,
        trigger,
        reason,
        bar_index,
        slippage_rate,
        fee_rate,
        equity,
    )
    return equity, None, trade


def _finish_trade(
    position: Dict[str, Any],
    trigger_price: float,
    exit_reason: str,
    bar_index: int,
    slippage_rate: float,
    fee_rate: float,
    equity: float,
) -> tuple[float, Dict[str, Any]]:
    quantity = position["remaining_qty"]
    if quantity > 0:
        equity = _close_quantity(
            position,
            quantity,
            trigger_price,
            position["side"],
            slippage_rate,
            fee_rate,
            equity,
        )

    costs = position["entry_fee"] + position["exit_fees"] + position["funding"]
    net_pnl = position["realized_gross"] - costs
    trade = {
        "side": "LONG" if position["side"] == 1 else "SHORT",
        "entry_time": position["entry_time"],
        "exit_time": position["exit_time"],
        "entry_price": position["entry"],
        "exit_price": trigger_price,
        "exit_reason": exit_reason,
        "bars_held": max(0, bar_index - position["entry_index"]),
        "net_pnl": net_pnl,
        "r_multiple": net_pnl / position["risk_budget"] if position["risk_budget"] else 0.0,
        "risk_budget": position["risk_budget"],
        "cost_r": costs / position["risk_budget"] if position["risk_budget"] else 0.0,
    }
    return equity, trade


def _is_opposing(position: Mapping[str, Any], signal: Mapping[str, Any], config: EvaluationConfig) -> bool:
    if config.opposing_exit_confidence is None or not signal:
        return False
    side = _signal_side(signal)
    return side == -position["side"] and _signal_confidence(signal) >= config.opposing_exit_confidence


def evaluate_signal_sequence(
    candles: Sequence[Mapping[str, Any]],
    signals: Sequence[Mapping[str, Any]],
    config: Optional[EvaluationConfig] = None,
    costs: Optional[CostModel] = None,
) -> Dict[str, Any]:
    """Evaluate signals using next-bar entries, intrabar stops, and explicit costs.

    A signal at index ``i`` can only trade the candle at ``i + 1``. When a
    candle touches both a stop and a target, the stop is assumed to execute
    first. This intentionally conservative rule prevents optimistic OHLC
    backtests from inflating win rate.
    """
    config = config or EvaluationConfig()
    costs = costs or CostModel()
    if len(candles) != len(signals):
        raise ValueError("candles and signals must have the same length")
    validate_klines(list(candles))
    if len(candles) < 2:
        raise ValueError("At least two candles are required")

    fee_rate = costs.fee_bps / 10000.0
    slippage_rate = costs.slippage_bps / 10000.0
    funding_rate = costs.funding_bps_per_bar / 10000.0
    equity = config.initial_equity
    peak_equity = equity
    max_drawdown = 0.0
    position: Optional[Dict[str, Any]] = None
    trades: List[Dict[str, Any]] = []
    skipped_low_confidence = 0
    skipped_invalid_signals = 0

    for index in range(1, len(candles)):
        candle = candles[index]
        close = float(candle["close"])

        if position is None:
            # Flat: try to open on the previous bar's signal, then manage it on this bar like any open position
            signal = signals[index - 1]
            if not signal:
                continue
            side = _signal_side(signal)
            if side is None or (side == -1 and not config.allow_short):
                continue
            if _signal_confidence(signal) < config.min_confidence_pct:
                skipped_low_confidence += 1
                continue

            entry = _entry_price(float(candle["open"]), side, slippage_rate)
            stop = _signal_price(signal, "stop_loss", "stopLoss")
            tp1 = _signal_price(signal, "take_profit_1", "takeProfit1")
            tp2 = _signal_price(signal, "take_profit_2", "takeProfit2")
            if not _targets_are_valid(side, entry, stop, tp1, tp2):
                skipped_invalid_signals += 1
                continue

            risk_per_unit = abs(entry - stop)
            risk_budget = equity * config.risk_per_trade
            quantity = risk_budget / risk_per_unit if risk_per_unit > 0 else 0.0
            max_notional = equity * config.max_leverage
            quantity = min(quantity, max_notional / entry if entry > 0 else 0.0)
            if quantity <= 0 or equity <= 0:
                skipped_invalid_signals += 1
                continue

            entry_fee = entry * quantity * fee_rate
            equity -= entry_fee
            position = {
                "side": side,
                "entry": entry,
                "entry_time": candles[index - 1].get("time"),
                "exit_time": candle.get("time"),
                "entry_index": index,
                "stop": stop,
                "tp1": tp1,
                "tp2": tp2,
                "tp1_hit": False,
                "be_moved": False,
                "remaining_qty": quantity,
                "realized_gross": 0.0,
                "entry_fee": entry_fee,
                "exit_fees": 0.0,
                "funding": 0.0,
                "risk_budget": risk_budget,
            }

        elif _is_opposing(position, signals[index - 1], config):
            position["exit_time"] = candle.get("time")
            equity, trade = _finish_trade(
                position, float(candle["open"]), "OPPOSING", index, slippage_rate, fee_rate, equity,
            )
            trades.append(trade)
            position = None
            marked_equity = equity
            peak_equity = max(peak_equity, marked_equity)
            max_drawdown = max(max_drawdown, (peak_equity - marked_equity) / max(peak_equity, 1e-12) * 100.0)
            continue

        equity, position, trade = _process_position(
            position,
            candle,
            index,
            equity,
            slippage_rate,
            fee_rate,
            funding_rate,
            config,
        )
        if trade is not None:
            trades.append(trade)
        marked_equity = _mark_equity(equity, position, close)
        peak_equity = max(peak_equity, marked_equity)
        max_drawdown = max(max_drawdown, (peak_equity - marked_equity) / max(peak_equity, 1e-12) * 100.0)

    if position is not None:
        final_candle = candles[-1]
        position["exit_time"] = final_candle.get("time")
        equity, trade = _finish_trade(
            position,
            float(final_candle["close"]),
            "END_OF_DATA",
            len(candles) - 1,
            slippage_rate,
            fee_rate,
            equity,
        )
        trades.append(trade)
        marked_equity = equity
        peak_equity = max(peak_equity, marked_equity)
        max_drawdown = max(max_drawdown, (peak_equity - marked_equity) / max(peak_equity, 1e-12) * 100.0)

    wins = [trade for trade in trades if trade["net_pnl"] > 0]
    losses = [trade for trade in trades if trade["net_pnl"] < 0]
    gross_profit = sum(trade["net_pnl"] for trade in wins)
    gross_loss = abs(sum(trade["net_pnl"] for trade in losses))
    net_profit = equity - config.initial_equity
    return {
        "initial_equity": config.initial_equity,
        "final_equity": equity,
        "net_profit": net_profit,
        "return_pct": net_profit / config.initial_equity * 100.0,
        "trade_count": len(trades),
        "win_rate_pct": len(wins) / len(trades) * 100.0 if trades else 0.0,
        "expectancy_r": sum(trade["r_multiple"] for trade in trades) / len(trades) if trades else 0.0,
        "avg_cost_r": sum(trade["cost_r"] for trade in trades) / len(trades) if trades else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else None,
        "max_drawdown_pct": max_drawdown,
        "skipped_low_confidence": skipped_low_confidence,
        "skipped_invalid_signals": skipped_invalid_signals,
        "trades": trades,
        "assumptions": {
            "entry": "previous candle signal, next candle open",
            "intrabar_conflict": "stop before target",
            "costs": {
                "fee_bps": costs.fee_bps,
                "slippage_bps": costs.slippage_bps,
                "funding_bps_per_bar": costs.funding_bps_per_bar,
            },
        },
    }
