class BudgetExceededError(Exception):
    def __init__(self, spent_usd: float, estimated_usd: float, limit_usd: float) -> None:
        self.spent_usd = spent_usd
        self.estimated_usd = estimated_usd
        self.limit_usd = limit_usd
        super().__init__(
            f"budget exceeded: spent=${spent_usd:.4f} + estimated=${estimated_usd:.4f} "
            f"> limit=${limit_usd:.4f}"
        )


class LLMCallError(Exception):
    pass
