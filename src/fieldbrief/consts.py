"""Constants shared across more than one module — values that would
otherwise drift out of sync if hardcoded independently in each file.
Module-local tables (tier budgets, confidence scoring, HTTP timeouts, ...)
stay next to the logic that uses them; this file is only for the rest.
"""

from datetime import timedelta

DEFAULT_FRESHNESS_MAX_AGE = timedelta(days=30)

WIKIDATA_CANDIDATE_LIMIT = 5

EXTRACTION_MAX_CHARS = 8000
COMPETITOR_DISCOVERY_MAX_CHARS = 6000

EXTRACT_NUM_RETRIES = 2
REPAIR_NUM_RETRIES = 1
