from fieldbrief.exceptions import BudgetExceededError


class Budget:
    def __init__(self, limit_usd: float) -> None:
        self.limit_usd = limit_usd
        self.spent_usd = 0.0

    def remaining_usd(self) -> float:
        return max(0.0, self.limit_usd - self.spent_usd)

    def check(self, estimated_usd: float) -> None:
        if self.spent_usd + estimated_usd > self.limit_usd:
            raise BudgetExceededError(self.spent_usd, estimated_usd, self.limit_usd)

    def reserve(self, estimated_usd: float) -> None:
        self.check(estimated_usd)
        self.spent_usd += estimated_usd

    def settle(self, usd: float) -> None:
        self.spent_usd += usd
