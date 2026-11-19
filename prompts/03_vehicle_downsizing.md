# Load-consolidation / vehicle downsizing notice to the planner

## Purpose
Tell the planner that a planned load will fill little of the vehicle and suggest a smaller vehicle or combining loads.

## Input fields
- `route`: route code
- `planned_vehicle`: planned vehicle type
- `planned_capacity`: planned capacity in MT
- `qty`: load in MT
- `utilisation_pct`: planned utilisation
- `suggested_capacity`: smallest capacity in MT seen on this route that fits the load
- `underload_probability`: V3 probability

## Prompt text
```text
Subject: Low planned load on {{route}}

Hi,

The planned load of {{qty}} MT on {{route}} fills {{utilisation_pct}}% of the {{planned_vehicle}} vehicle ({{planned_capacity}} MT). The under-load model gives a {{underload_probability}}% chance this truck leaves below 60% full.

Please consider a vehicle of about {{suggested_capacity}} MT, which has been used on this route, or combining this load with another shipment on the same lane.
```

The LLM may polish the tone of this text but must keep every `{{placeholder}}` exactly as written; numbers are inserted by code afterwards.

## Worked example
Input:
```json
{
  "route": "GCCOI1",
  "planned_vehicle": "OB27",
  "planned_capacity": 27.5,
  "qty": 4.0,
  "utilisation_pct": "15",
  "suggested_capacity": 23.0,
  "underload_probability": "0"
}
```

Output (before any LLM polish):
```text
Subject: Low planned load on GCCOI1

Hi,

The planned load of 4.0 MT on GCCOI1 fills 15% of the OB27 vehicle (27.5 MT). The under-load model gives a 0% chance this truck leaves below 60% full.

Please consider a vehicle of about 23.0 MT, which has been used on this route, or combining this load with another shipment on the same lane.
```

## Known failure modes
- The suggested capacity comes from history on the route; the vehicle may not be available.
- Under-load flag is a probability, not a certainty.
- Do not claim a saving unless a tool returned it.
