# Hub delay alert to the hub manager

## Purpose
Alert a hub manager that legs starting at their hub lose more time than the network median.

## Input fields
- `hub_name`: hub name
- `legs`: test legs
- `mean_handling_min`: mean handling minutes
- `network_median_min`: network median handling minutes
- `excess_hours`: hours above the network median
- `excess_cost`: cost of the excess hours (sourced synthetic rate)

## Prompt text
```text
Subject: Handling time at {{hub_name}}

Hi,

Legs starting at {{hub_name}} averaged {{mean_handling_min}} minutes of handling beyond driving time across {{legs}} legs, against a network median of {{network_median_min}} minutes. That is about {{excess_hours}} hours above the median, roughly {{excess_cost}} at the planning rate.

Could you review loading and dispatch practices and share what is driving it?
```

The LLM may polish the tone of this text but must keep every `{{placeholder}}` exactly as written; numbers are inserted by code afterwards.

## Worked example
Input:
```json
{
  "hub_name": "Gurgaon_Bilaspur_HB (Haryana)",
  "legs": 286,
  "mean_handling_min": "189",
  "network_median_min": "51",
  "excess_hours": "659",
  "excess_cost": "₹2.8 lakh"
}
```

Output (before any LLM polish):
```text
Subject: Handling time at Gurgaon_Bilaspur_HB (Haryana)

Hi,

Legs starting at Gurgaon_Bilaspur_HB (Haryana) averaged 189 minutes of handling beyond driving time across 286 legs, against a network median of 51 minutes. That is about 659 hours above the median, roughly ₹2.8 lakh at the planning rate.

Could you review loading and dispatch practices and share what is driving it?
```

## Known failure modes
- Rupee figure uses sourced synthetic rates, not actual costs.
- Based on one week of 2018 data; say so if sent outside the project.
- Handling time = scan time minus driving time, so recording errors show up as delay.
