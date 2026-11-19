# Revised ETA note to the customer

## Purpose
Plain-language note giving a customer a realistic travel time instead of the planned one.

## Input fields
- `trip_ref`: trip reference
- `planned_hours`: planned travel hours
- `expected_hours`: model's expected travel hours
- `range_low_hours`: low end of range
- `range_high_hours`: high end of range

## Prompt text
```text
Subject: Update on your shipment {{trip_ref}}

Hello,

This shipment was planned for about {{planned_hours}} hours on the road. Based on how similar journeys have run, we now expect around {{expected_hours}} hours, most likely between {{range_low_hours}} and {{range_high_hours}} hours. We will update you if anything changes.

Thank you for your patience.
```

The LLM may polish the tone of this text but must keep every `{{placeholder}}` exactly as written; numbers are inserted by code afterwards.

## Worked example
Input:
```json
{
  "trip_ref": "trip-153809667984245845",
  "planned_hours": "0.3",
  "expected_hours": "1.1",
  "range_low_hours": "0.6",
  "range_high_hours": "1.6"
}
```

Output (before any LLM polish):
```text
Subject: Update on your shipment trip-153809667984245845

Hello,

This shipment was planned for about 0.3 hours on the road. Based on how similar journeys have run, we now expect around 1.1 hours, most likely between 0.6 and 1.6 hours. We will update you if anything changes.

Thank you for your patience.
```

## Known failure modes
- The range is indicative: the model's nominal 80% interval covers about 67% of trips.
- Customer-facing: do not mention model flags or internal drivers.
- Based on 2018 data.
