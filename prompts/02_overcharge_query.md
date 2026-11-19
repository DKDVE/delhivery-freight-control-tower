# Overcharge query on a specific lane

## Purpose
Query a quoted or invoiced rate that is well above the model's fair price for the lane.

## Input fields
- `vendor`: vendor name
- `route`: route code
- `vehicle_type`: vehicle type code
- `quoted_rs`: quoted amount
- `fair_rs`: model fair price
- `overpay_pct`: percent above fair price
- `excess_rs`: amount above fair price

## Prompt text
```text
Subject: Query on quoted freight for {{route}} ({{vehicle_type}})

Dear {{vendor}} team,

The quote of {{quoted_rs}} for this movement is {{overpay_pct}}% above the benchmark of {{fair_rs}} we see for comparable loads on the same lane and vehicle type, a difference of {{excess_rs}}.

Please confirm the basis of the quote (distance, waiting, return load or other charges) so we can reconcile it before booking.

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
  "quoted_rs": "₹168,419",
  "fair_rs": "₹27,079",
  "overpay_pct": "522.0",
  "excess_rs": "₹141,340"
}
```

Output (before any LLM polish):
```text
Subject: Query on quoted freight for GCCOI1 (OB27)

Dear V002 team,

The quote of ₹168,419 for this movement is 522.0% above the benchmark of ₹27,079 we see for comparable loads on the same lane and vehicle type, a difference of ₹141,340.

Please confirm the basis of the quote (distance, waiting, return load or other charges) so we can reconcile it before booking.

Regards,
Freight Procurement, Delhivery
```

## Known failure modes
- The fair price is a market median for the lane profile, not the vendor's cost: do not present it as an invoice error.
- Wide prediction range on rare lanes: check the fair_low / fair_high range.
- Never send automatically; the planner approves.
