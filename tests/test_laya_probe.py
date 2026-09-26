import pytest

from experiments.laya_probe import TRAINING_QUESTION, summarize


def test_summarize_perfect_and_useless_rankers():
    hit = [False] * 90 + [True] * 10
    perfect = summarize([0.1] * 90 + [0.9] * 10, hit)
    assert perfect["auc"] == pytest.approx(1.0)
    assert perfect["top_decile_hit_rate"] == pytest.approx(1.0)
    assert perfect["base_rate"] == pytest.approx(0.1)

    flat = summarize([0.5] * 100, hit)
    assert flat["auc"] == pytest.approx(0.5)


def test_probe_asks_the_training_question():
    criteria = TRAINING_QUESTION["action"]["criteria"]
    assert list(criteria) == ["LONG", "SHORT", "NO_TRADE"]
    assert "+1.2% TP before -0.6% SL within 2 hours" in criteria["LONG"]
