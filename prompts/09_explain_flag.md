# Explain a model flag to a non-technical manager

## Purpose
Turn a V2 loss-risk flag and its top drivers into plain English with one recommended action.

## Input fields
- `route`: route code
- `vendor`: vendor name
- `probability_pct`: loss probability
- `threshold_pct`: flag threshold
- `driver1`: top driver
- `driver2`: second driver
- `driver3`: third driver
- `recommended_action`: one action

## Prompt text
```text
Why this booking was flagged

Booking on {{route}} with {{vendor}} has a {{probability_pct}}% estimated chance of losing money (flag threshold {{threshold_pct}}%).

Main reasons: {{driver1}}; {{driver2}}; {{driver3}}.

Suggested action: {{recommended_action}}. A person decides; nothing changes automatically.
```

The LLM may polish the tone of this text but must keep every `{{placeholder}}` exactly as written; numbers are inserted by code afterwards.

## Worked example
Input:
```json
{
  "route": "GCCOI1",
  "vendor": "V002",
  "probability_pct": "99",
  "threshold_pct": "54",
  "driver1": "route = GCCOI1 (raises loss risk)",
  "driver2": "shipment_utilisation = 14.545454545454545 (raises loss risk)",
  "driver3": "month = 2 (raises loss risk)",
  "recommended_action": "renegotiate the rate for this lane or reassign the vendor"
}
```

Output (before any LLM polish):
```text
Why this booking was flagged

Booking on GCCOI1 with V002 has a 99% estimated chance of losing money (flag threshold 54%).

Main reasons: route = GCCOI1 (raises loss risk); shipment_utilisation = 14.545454545454545 (raises loss risk); month = 2 (raises loss risk).

Suggested action: renegotiate the rate for this lane or reassign the vendor. A person decides; nothing changes automatically.
```

## Known failure modes
- SHAP drivers describe the model, not causes.
- A probability is not a guarantee.
- Driver values like 'route' are codes; add plain names where known.
