# Data-quality issue report

## Purpose
Report a data issue (for example vehicle_capacity = 0) with its size and effect on the models.

## Input fields
- `issue`: short name of the issue
- `field`: affected column
- `count`: affected rows
- `share_pct`: share of all rows
- `impact`: how it affects the analysis
- `suggested_fix`: suggested fix at source

## Prompt text
```text
Data-quality report: {{issue}}

Column {{field}} has {{count}} affected rows ({{share_pct}}% of the vendor freight file). Impact: {{impact}}.

Suggested fix: {{suggested_fix}}.
```

The LLM may polish the tone of this text but must keep every `{{placeholder}}` exactly as written; numbers are inserted by code afterwards.

## Worked example
Input:
```json
{
  "issue": "Vehicle capacity zero or missing",
  "field": "vehicle_capacity",
  "count": 17211,
  "share_pct": "5.8",
  "impact": "truck utilisation cannot be computed, so those shipments are excluded from the under-load model",
  "suggested_fix": "make vehicle capacity mandatory when a shipment is created, or derive it from the vehicle type"
}
```

Output (before any LLM polish):
```text
Data-quality report: Vehicle capacity zero or missing

Column vehicle_capacity has 17,211 affected rows (5.8% of the vendor freight file). Impact: truck utilisation cannot be computed, so those shipments are excluded from the under-load model.

Suggested fix: make vehicle capacity mandatory when a shipment is created, or derive it from the vehicle type.
```

## Known failure modes
- Counts are from the raw file before cleaning.
- Do not guess the cause of the issue; the fix must come from the data owner.
- Keep the report factual.
