from fieldbrief.db.models import Base

EXPECTED_TABLES = {
    "users",
    "companies",
    "company_competitors",
    "sources",
    "claims",
    "reports",
    "report_competitors",
    "report_claims",
}


def test_all_eight_tables_registered() -> None:
    assert {t.name for t in Base.metadata.sorted_tables} == EXPECTED_TABLES
