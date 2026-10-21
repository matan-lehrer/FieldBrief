"""Per-report cost budget, shared by every node in one report's graph run.

design.md "LLM gateway" step 3: "Budget: estimate, reserve from report budget;
near limit -> downgrade or stop." MVP simplifies "downgrade" to "stop" (ADR
0010's stop conditions already treat cost budget as a report-wide cap that
just ends the retry loop, not something that swaps models mid-report).
"""


class BudgetExceededError(Exception):
    """Raised when a call would push spend past the report's budget."""

    def __init__(self, spent_usd: float, estimated_usd: float, limit_usd: float) -> None:
        self.spent_usd = spent_usd
        self.estimated_usd = estimated_usd
        self.limit_usd = limit_usd
        super().__init__(
            f"budget exceeded: spent=${spent_usd:.4f} + estimated=${estimated_usd:.4f} "
            f"> limit=${limit_usd:.4f}"
        )


class Budget:
    """Tracks one report's spend. Not thread-safe across workers — one report,
    one LangGraph run, one worker (ADR 0001)."""

    def __init__(self, limit_usd: float) -> None:
        self.limit_usd = limit_usd
        self.spent_usd = 0.0

    def remaining_usd(self) -> float:
        return max(0.0, self.limit_usd - self.spent_usd)

    def check(self, estimated_usd: float) -> None:
        """Raise before making a call that would exceed the budget."""
        if self.spent_usd + estimated_usd > self.limit_usd:
            raise BudgetExceededError(self.spent_usd, estimated_usd, self.limit_usd)

    def settle(self, actual_usd: float) -> None:
        self.spent_usd += actual_usd
