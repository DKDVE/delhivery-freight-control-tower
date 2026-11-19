# Escalation for a vendor with repeated loss-making LRs

## Purpose
Escalate to the procurement head when a vendor's consignments keep losing money.

## Input fields
- `vendor`: vendor name
- `lrs`: number of LRs
- `loss_share_pct`: share of loss-making LRs
- `loss_rs`: total Rs lost on loss-making LRs
- `worst_lane`: lane with the largest losses
- `worst_lane_loss`: Rs lost on that lane
- `period`: data period

## Prompt text
```text
Subject: Escalation: repeated loss-making consignments with {{vendor}}

Over {{period}}, {{loss_share_pct}}% of the {{lrs}} consignments with {{vendor}} lost money, a total of {{loss_rs}}. The largest single lane is {{worst_lane}}, with {{worst_lane_loss}} lost.

Recommended next step for review: renegotiate the rate card for the worst lanes or move that volume to another approved vendor. No vendor action is taken until you approve.
```

The LLM may polish the tone of this text but must keep every `{{placeholder}}` exactly as written; numbers are inserted by code afterwards.

## Worked example
Input:
```json
{
  "vendor": "V002",
  "lrs": 8443,
  "loss_share_pct": "37.5",
  "loss_rs": "₹1.50 Cr",
  "worst_lane": "GCNEE1 / OB29",
  "worst_lane_loss": "₹9.1 lakh",
  "period": "2025-04-01 to 2026-03-30"
}
```

Output (before any LLM polish):
```text
Subject: Escalation: repeated loss-making consignments with V002

Over 2025-04-01 to 2026-03-30, 37.5% of the 8,443 consignments with V002 lost money, a total of ₹1.50 Cr. The largest single lane is GCNEE1 / OB29, with ₹9.1 lakh lost.

Recommended next step for review: renegotiate the rate card for the worst lanes or move that volume to another approved vendor. No vendor action is taken until you approve.
```

## Known failure modes
- Must not recommend terminating or blacklisting a vendor (never automated).
- Losses depend on the customer billing as well as vendor cost.
- A loss-making LR is not necessarily the vendor's fault.
