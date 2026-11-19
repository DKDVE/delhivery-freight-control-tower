"""Scenario cost rates (Rs per hour) for the middle-mile business-impact inputs. Sourced synthetic: not Delhivery actuals."""

RATE_SOURCE = "sourced synthetic"

RATES_PER_HOUR = {
    "base": {"FTL": 565, "Carting": 125},
    "conservative": {"FTL": 500, "Carting": 90},
    "optimistic": {"FTL": 630, "Carting": 160},
}
