from fieldbrief.agents.field_checklist import fields_for


def test_quick_target_has_eight_fields_including_events() -> None:
    fields = fields_for("quick", is_target=True)
    assert len(fields) == 8
    assert "events.recent" in fields


def test_quick_competitor_excludes_target_only_fields() -> None:
    fields = fields_for("quick", is_target=False)
    assert "events.recent" not in fields
    assert len(fields) == 7


def test_standard_is_cumulative_with_quick() -> None:
    quick_fields = set(fields_for("quick", is_target=True))
    standard_fields = set(fields_for("standard", is_target=True))
    assert quick_fields <= standard_fields
    assert "market.forces" in standard_fields
    assert "resources.advantages" in standard_fields


def test_standard_competitor_excludes_target_only_fields() -> None:
    fields = fields_for("standard", is_target=False)
    assert "market.forces" not in fields
    assert "resources.advantages" not in fields
    assert "pricing.model" in fields  # not target-only


def test_deep_is_cumulative_with_standard() -> None:
    standard_fields = set(fields_for("standard", is_target=True))
    deep_fields = set(fields_for("deep", is_target=True))
    assert standard_fields <= deep_fields
    assert "risks.disclosed" in deep_fields
