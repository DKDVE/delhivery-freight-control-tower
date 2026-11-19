# Weekly control-tower digest (top risks)

## Purpose
Short digest of the biggest cost and delay risks, built from query_data results.

## Input fields
- `period`: reporting period
- `top_overpay_vendor`: vendor with the largest excess
- `top_overpay_excess`: that vendor's excess
- `top_loss_vendor`: vendor with the largest losses
- `top_loss_amount`: its loss
- `worst_hub`: hub with the most excess hours
- `worst_hub_hours`: its excess hours
- `worst_corridor`: corridor with the highest median overrun
- `worst_corridor_ratio`: its median actual / planned

## Prompt text
```text
Control-tower digest: {{period}}

1. Price: {{top_overpay_vendor}} shows the largest excess over benchmark price ({{top_overpay_excess}}).
2. Losses: {{top_loss_vendor}} has the largest loss on loss-making consignments ({{top_loss_amount}}).
3. Hubs: {{worst_hub}} has the most excess handling time ({{worst_hub_hours}} hours above the median).
4. Corridors: {{worst_corridor}} runs at a median {{worst_corridor_ratio}}x its planned time.

All figures come from the approved queries; actions need your approval.
```

The LLM may polish the tone of this text but must keep every `{{placeholder}}` exactly as written; numbers are inserted by code afterwards.

## Worked example
Input:
```json
{
  "period": "latest data period",
  "top_overpay_vendor": "V002",
  "top_overpay_excess": "₹0.78 Cr",
  "top_loss_vendor": "V003",
  "top_loss_amount": "₹1.73 Cr",
  "worst_hub": "Gurgaon_Bilaspur_HB (Haryana)",
  "worst_hub_hours": "659",
  "worst_corridor": "Bihar -> Bihar",
  "worst_corridor_ratio": "3.0"
}
```

Output (before any LLM polish):
```text
Control-tower digest: latest data period

1. Price: V002 shows the largest excess over benchmark price (₹0.78 Cr).
2. Losses: V003 has the largest loss on loss-making consignments (₹1.73 Cr).
3. Hubs: Gurgaon_Bilaspur_HB (Haryana) has the most excess handling time (659 hours above the median).
4. Corridors: Bihar -> Bihar runs at a median 3.0x its planned time.

All figures come from the approved queries; actions need your approval.
```

## Known failure modes
- Mixes two datasets (2025-26 vendor file and 2018 trips): label them if the audience might confuse them.
- A single top item per list hides the long tail.
- Not a forecast.
