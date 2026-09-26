"""Barrier outcome labels: did a trade entered at the next open hit its target before its stop?"""

from typing import Tuple

import numpy as np


def _first_hit(hits: np.ndarray) -> np.ndarray:
    """Index of the first True per row, or a sentinel past the horizon when none."""
    return np.where(hits.any(axis=1), hits.argmax(axis=1), hits.shape[1])


def barrier_labels(open_: np.ndarray, high: np.ndarray, low: np.ndarray, tp: float = 0.012,
                   sl: float = 0.006, horizon: int = 24) -> Tuple[np.ndarray, np.ndarray]:
    """Long and short outcomes for a signal on each bar.

    Entry is the next bar's open; bars i+1..i+horizon decide. 1.0 = target hit
    first, 0.0 = stop first, both in the same bar, or neither by the horizon.
    Bars without a full horizon ahead are NaN.
    """
    n = len(open_)
    long_win = np.full(n, np.nan)
    short_win = np.full(n, np.nan)
    m = n - horizon  # signals 0..m-1 have bars i+1..i+horizon
    if m <= 0:
        return long_win, short_win

    entry = open_[1:m + 1]
    # future[:, k] is bar i+1+k for signal i
    future_high = np.lib.stride_tricks.sliding_window_view(high[1:], horizon)[:m]
    future_low = np.lib.stride_tricks.sliding_window_view(low[1:], horizon)[:m]

    # A stop in the same bar as the target wins the tie: stop index <= target index is a loss.
    long_tp = _first_hit(future_high >= (entry * (1 + tp))[:, None])
    long_sl = _first_hit(future_low <= (entry * (1 - sl))[:, None])
    short_tp = _first_hit(future_low <= (entry * (1 - tp))[:, None])
    short_sl = _first_hit(future_high >= (entry * (1 + sl))[:, None])

    long_win[:m] = (long_tp < long_sl).astype(float)
    short_win[:m] = (short_tp < short_sl).astype(float)
    return long_win, short_win
