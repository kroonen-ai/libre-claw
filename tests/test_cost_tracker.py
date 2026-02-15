"""Tests for cost tracking."""

from libre_claw.utils.cost_tracker import CostTracker


def test_empty_tracker():
    tracker = CostTracker()
    assert tracker.total_tokens == 0
    assert tracker.estimate_cost() == 0.0


def test_record_usage():
    tracker = CostTracker()
    tracker.record(model="claude-code", input_tokens=1000, output_tokens=500, backend="claude-code")

    assert tracker.total_input_tokens == 1000
    assert tracker.total_output_tokens == 500
    assert tracker.total_tokens == 1500


def test_cost_estimate():
    tracker = CostTracker()
    # 1M input + 1M output at claude-code rates ($3/$15)
    tracker.record(model="claude-code", input_tokens=1_000_000, output_tokens=1_000_000)

    cost = tracker.estimate_cost()
    assert cost == 18.0  # $3 input + $15 output


def test_ollama_free():
    tracker = CostTracker()
    tracker.record(model="ollama", input_tokens=1_000_000, output_tokens=1_000_000, backend="ollama")

    assert tracker.estimate_cost() == 0.0


def test_summary():
    tracker = CostTracker()
    tracker.record(model="claude-code", input_tokens=100, output_tokens=50)

    summary = tracker.summary()
    assert summary["total_records"] == 1
    assert summary["total_tokens"] == 150
