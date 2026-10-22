"""Per-report cost budget, shared across one report's graph run."""

from fieldbrief.exceptions import BudgetExceededError


class Budget:
    """Tracks one report's spend. Not thread-safe across workers."""

    def __init__(self, limit_usd: float) -> None:
        self.limit_usd = limit_usd
        self.spent_usd = 0.0

    def remaining_usd(self) -> float:
        return max(0.0, self.limit_usd - self.spent_usd)

    def check(self, estimated_usd: float) -> None:
        """Raise if a call of this size wouldn't fit, without reserving it."""
        if self.spent_usd + estimated_usd > self.limit_usd:
            raise BudgetExceededError(self.spent_usd, estimated_usd, self.limit_usd)

    def reserve(self, estimated_usd: float) -> None:
        """Atomic check-and-commit, safe across concurrent callers sharing
        one Budget. Pair with settle(actual - estimated) once known."""
        self.check(estimated_usd)
        self.spent_usd += estimated_usd

    def settle(self, usd: float) -> None:
        """Add a (possibly negative) delta to spend."""
        self.spent_usd += usd
