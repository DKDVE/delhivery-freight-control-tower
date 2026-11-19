# Vendor rate renegotiation request

## Purpose
Ask a vendor to move a lane's rate toward the observed benchmark. Used after check_booking or vendor_profile shows the vendor above the fair price.

## Input fields
- `vendor`: vendor name
- `route`: route code
- `vehicle_type`: vehicle type code
- `current_rate`: vendor's volume-weighted Rs per MT on the lane
- `benchmark_rate`: cheapest alternative vendor's Rs per MT
- `lr_count`: vendor's LRs on the lane
- `period`: data period

## Prompt text
```text
Subject: Rate review for lane {{route}} ({{vehicle_type}})

Dear {{vendor}} team,

Over {{period}} you carried {{lr_count}} consignments for us on lane {{route}} with vehicle type {{vehicle_type}} at an average of {{current_rate}} per MT. Other approved vendors on the same lane and vehicle type are currently moving freight at about {{benchmark_rate}} per MT.

We would like to review the rate for this lane with you. Could you share a revised proposal this week?

Regards,
Freight Procurement, Delhivery
```

The LLM may polish the tone of this text but must keep every `{{placeholder}}` exactly as written; numbers are inserted by code afterwards.

## Worked example
Input:
```json
{
  "vendor": "V002",
  "route": "GCCOI1",
  "vehicle_type": "OB27",
  "current_rate": "₹4,785",
  "benchmark_rate": "₹4,518",
  "lr_count": 50,
  "period": "April 2025 to March 2026"
}
```

Output (before any LLM polish):
```text
Subject: Rate review for lane GCCOI1 (OB27)

Dear V002 team,

Over April 2025 to March 2026 you carried 50 consignments for us on lane GCCOI1 with vehicle type OB27 at an average of ₹4,785 per MT. Other approved vendors on the same lane and vehicle type are currently moving freight at about ₹4,518 per MT.

We would like to review the rate for this lane with you. Could you share a revised proposal this week?

Regards,
Freight Procurement, Delhivery
```

## Known failure modes
- Tone too aggressive if the LLM 'polishes' too hard; keep it factual.
- Benchmark can come from a single vendor with few LRs: the tool requires 10+ LRs, check lr_count before sending.
- Anonymised vendor names do not map to real contracts.
