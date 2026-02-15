"""Cost tracking for Libre Claw.

Tracks token usage and estimates costs across backends.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List

# Approximate pricing per 1M tokens (USD)
PRICING = {
    "claude-opus-4": {"input": 15.0, "output": 75.0},
    "claude-sonnet-4": {"input": 3.0, "output": 15.0},
    "claude-haiku-3.5": {"input": 0.80, "output": 4.0},
    "claude-code": {"input": 3.0, "output": 15.0},  # Estimate (uses Sonnet)
    "ollama": {"input": 0.0, "output": 0.0},  # Local, no cost
}


@dataclass
class UsageRecord:
    timestamp: datetime
    model: str
    input_tokens: int
    output_tokens: int
    backend: str


@dataclass
class CostTracker:
    """Tracks token usage and estimated costs."""

    records: List[UsageRecord] = field(default_factory=list)

    def record(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        backend: str = "unknown",
    ) -> None:
        self.records.append(UsageRecord(
            timestamp=datetime.now(),
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            backend=backend,
        ))

    @property
    def total_input_tokens(self) -> int:
        return sum(r.input_tokens for r in self.records)

    @property
    def total_output_tokens(self) -> int:
        return sum(r.output_tokens for r in self.records)

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    def estimate_cost(self) -> float:
        """Estimate total cost in USD."""
        total = 0.0
        for r in self.records:
            pricing = PRICING.get(r.model, PRICING.get(r.backend, {"input": 0, "output": 0}))
            total += (r.input_tokens / 1_000_000) * pricing["input"]
            total += (r.output_tokens / 1_000_000) * pricing["output"]
        return total

    def summary(self) -> Dict[str, any]:
        return {
            "total_records": len(self.records),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": round(self.estimate_cost(), 4),
        }
