import pytest

from fieldbrief.llm.budget import Budget, BudgetExceededError


def test_check_passes_within_budget() -> None:
    budget = Budget(limit_usd=1.0)
    budget.check(0.5)  # must not raise


def test_check_raises_when_would_exceed() -> None:
    budget = Budget(limit_usd=1.0)
    budget.settle(0.9)
    with pytest.raises(BudgetExceededError):
        budget.check(0.2)


def test_remaining_usd_floors_at_zero() -> None:
    budget = Budget(limit_usd=1.0)
    budget.settle(5.0)
    assert budget.remaining_usd() == 0.0


def test_settle_accumulates() -> None:
    budget = Budget(limit_usd=1.0)
    budget.settle(0.1)
    budget.settle(0.2)
    assert budget.spent_usd == pytest.approx(0.3)


def test_reserve_commits_the_estimate_immediately() -> None:
    budget = Budget(limit_usd=1.0)
    budget.reserve(0.4)
    assert budget.spent_usd == pytest.approx(0.4)
    assert budget.remaining_usd() == pytest.approx(0.6)


def test_reserve_raises_without_mutating_when_it_would_exceed() -> None:
    budget = Budget(limit_usd=1.0)
    budget.reserve(0.9)
    with pytest.raises(BudgetExceededError):
        budget.reserve(0.2)
    assert budget.spent_usd == pytest.approx(0.9)  # the failed reserve didn't partially apply


def test_reserve_then_settle_trues_up_to_the_real_cost() -> None:
    budget = Budget(limit_usd=1.0)
    budget.reserve(0.1)  # estimate
    budget.settle(0.15 - 0.1)  # actual turned out higher
    assert budget.spent_usd == pytest.approx(0.15)
