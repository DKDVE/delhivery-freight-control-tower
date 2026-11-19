# Vendor quarterly performance summary

## Purpose
One-paragraph performance summary for a vendor review meeting.

## Input fields
- `vendor`: vendor name
- `period`: data period
- `lrs`: number of LRs
- `spend`: freight paid
- `margin_pct`: margin % on billed value
- `loss_share_pct`: share of loss-making LRs
- `overpay_pct`: percent above fair price in the test quarter
- `worst_lane`: lane with the largest losses

## Prompt text
```text
Vendor summary: {{vendor}} ({{period}})

{{vendor}} handled {{lrs}} consignments with freight paid of {{spend}}. Margin on billed value was {{margin_pct}}%, and {{loss_share_pct}}% of consignments lost money. In the latest quarter its freight ran {{overpay_pct}}% above the benchmark price for comparable loads. The lane with the largest losses is {{worst_lane}}.
```

The LLM may polish the tone of this text but must keep every `{{placeholder}}` exactly as written; numbers are inserted by code afterwards.

## Worked example
Input:
```json
{
  "vendor": "V002",
  "period": "2025-04-01 to 2026-03-30",
  "lrs": 8443,
  "spend": "₹39.98 Cr",
  "margin_pct": "3.6",
  "loss_share_pct": "37.5",
  "overpay_pct": "3.6",
  "worst_lane": "GCNEE1 / OB29"
}
```

Output (before any LLM polish):
```text
Vendor summary: V002 (2025-04-01 to 2026-03-30)

V002 handled 8,443 consignments with freight paid of ₹39.98 Cr. Margin on billed value was 3.6%, and 37.5% of consignments lost money. In the latest quarter its freight ran 3.6% above the benchmark price for comparable loads. The lane with the largest losses is GCNEE1 / OB29.
```

## Known failure modes
- Vendors with fewer than 10 LRs are not ranked; do not draft a summary for them.
- Margin is on valid-cost LRs only (imputed freight excluded).
- overpay_pct is None for vendors without test-quarter shipments.
