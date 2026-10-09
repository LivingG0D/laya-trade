"""Does the fine-tuned Laya model carry any signal when queried the way it was trained?

Runs the native model over the held-out validation file written by
``prepare_full_mtf_laya.py`` (15m BTC states, LONG / SHORT / NO_TRADE barrier
question) with exactly the training state and question, then reports how well
P(LONG) and P(SHORT) rank the true outcomes.

Run:  python experiments/laya_probe.py --val "X:/laya btc datasets/laya_mtf_val.jsonl"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Dict, List, Sequence

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experiments.harness import BARRIER_BREAK_EVEN  # noqa: E402
from src.config import BASE_DIR  # noqa: E402

TRAINING_QUESTION = {
    "action": {
        "type": "choice",
        "instructions": "Determine optimal trade action based on multi-timeframe confluence and microstructure.",
        "criteria": {
            "LONG": "Upward expansion hitting +1.2% TP before -0.6% SL within 2 hours",
            "SHORT": "Downward expansion hitting -1.2% TP before +0.6% SL within 2 hours",
            "NO_TRADE": "Sideways chop, stop-loss triggered first, or no barrier hit within 2 hours",
        },
    }
}
BREAK_EVEN = BARRIER_BREAK_EVEN  # win rate needed for +1.2% / -0.6% after round-trip costs


def summarize(p_side: Sequence[float], hit: Sequence[bool]) -> Dict[str, float]:
    """How well one side's probability ranks its true outcome."""
    from sklearn.metrics import roc_auc_score

    p = np.asarray(p_side, dtype=float)
    y = np.asarray(hit, dtype=bool)
    top = p >= np.quantile(p, 0.9)
    top1 = p >= np.quantile(p, 0.99)
    return {
        "base_rate": float(y.mean()),
        "auc": float(roc_auc_score(y, p)) if 0 < y.sum() < len(y) else float("nan"),
        "top_decile_hit_rate": float(y[top].mean()),
        "top_percentile_hit_rate": float(y[top1].mean()),
        "mean_p": float(p.mean()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--val", required=True, help="laya_mtf_val.jsonl from the fine-tune pipeline")
    parser.add_argument("--limit", type=int, default=0, help="only the first N rows (0 = all)")
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args()

    records: List[dict] = []
    with open(args.val, encoding="utf-8") as fh:
        for line in fh:
            records.append(json.loads(line))
            if args.limit and len(records) >= args.limit:
                break
    print(f"{len(records)} validation rows {records[0]['datetime']} -> {records[-1]['datetime']}", file=sys.stderr)

    import asyncio

    from src.laya_engine import LayaEngine

    engine = LayaEngine()
    if not asyncio.run(engine.start_server()) or engine._agent is None:
        print("Native Laya model did not load.", file=sys.stderr)
        return 1

    probs = {"LONG": [], "SHORT": [], "NO_TRADE": []}
    started = time.time()
    for n in range(0, len(records), 2048):
        chunk = records[n:n + 2048]
        results = engine._agent.predict_batch([r["state"] for r in chunk], TRAINING_QUESTION,
                                              batch_size=args.batch_size)
        for result in results:
            p = result["answers"]["action"]["probabilities"]
            for key in probs:
                probs[key].append(float(p[key]))
        print(f"  {min(n + 2048, len(records))}/{len(records)} ({time.time() - started:.0f}s)", file=sys.stderr)

    targets = np.array([r["target"] for r in records])
    predicted = np.array(["LONG", "SHORT", "NO_TRADE"])[
        np.argmax(np.column_stack([probs["LONG"], probs["SHORT"], probs["NO_TRADE"]]), axis=1)]
    report = {
        "rows": len(records),
        "period": [records[0]["datetime"], records[-1]["datetime"]],
        "accuracy": float((predicted == targets).mean()),
        "always_no_trade_accuracy": float((targets == "NO_TRADE").mean()),
        "predicted_counts": {k: int((predicted == k).sum()) for k in probs},
        "long": summarize(probs["LONG"], targets == "LONG"),
        "short": summarize(probs["SHORT"], targets == "SHORT"),
        "break_even_hit_rate": BREAK_EVEN,
    }
    out = BASE_DIR / "scratch" / "backtests" / f"laya_probe_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    print(f"Saved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
