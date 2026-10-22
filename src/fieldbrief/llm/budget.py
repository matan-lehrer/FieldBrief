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
        """Raise if a call of this estimated size wouldn't fit. A pure,
        non-mutating look — prefer reserve() for a spend you intend to
        settle() afterwards; this is for a dry-run check only."""
        if self.spent_usd + estimated_usd > self.limit_usd:
            raise BudgetExceededError(self.spent_usd, estimated_usd, self.limit_usd)

    def reserve(self, estimated_usd: float) -> None:
        """Atomically check-and-commit an estimate, synchronously (no
        `await` in between) — this is what makes a Budget shared across
        concurrent callers (agents/orchestrator.py's asyncio.gather over
        per-company research) actually safe. check() used to be called,
        then an LLM call was awaited, then settle() — two concurrent callers
        could both pass check() before either had settled, overshooting the
        budget. Pair with settle(actual_usd - estimated_usd) once the real
        cost is known, to true up without double-counting the reservation."""
        self.check(estimated_usd)
        self.spent_usd += estimated_usd

    def settle(self, usd: float) -> None:
        """Add a (possibly negative) delta to spend — on its own, a plain
        accumulator; after reserve(estimated), pass (actual - estimated)."""
        self.spent_usd += usd
