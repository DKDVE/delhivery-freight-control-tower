"""Chart selection: code decides the chart type from the result's column roles/units and row count.

Deterministic and independent of any dataframe or tool output. Decision table, first match wins:
  one row, one measure                                   -> kpi_tile
  a time column + 1-3 measures                            -> line
  one dimension + one measure, <= 25 rows                 -> bar  (or stacked100 if that measure's unit is %)
  one dimension + 2 measures with different units          -> bar_dot
  2 measures, no dimension, > 10 rows                      -> scatter
  anything else (> 25 rows, or nothing above fits)         -> table
There is no pie chart: it is not in the allowed list.
"""
from __future__ import annotations

ALLOWED_TYPES = ("kpi_tile", "line", "bar", "stacked100", "bar_dot", "scatter", "table")


def choose_chart(columns: list[dict], row_count: int, title_hint: str | None = None) -> dict:
    """columns: [{"name","label","role" in (dimension,measure,time),"unit",...}, ...] for the columns actually
    on display (not necessarily every column the query can return). Returns {"type", "x", "y": [...], "title"}."""
    dims = [c for c in columns if c["role"] == "dimension"]
    times = [c for c in columns if c["role"] == "time"]
    measures = [c for c in columns if c["role"] == "measure"]

    if row_count == 1 and len(measures) == 1 and not times:
        # a single row is a single number, whether or not a dimension column also came back (e.g. the user
        # filtered a grouped query down to one group) - a lone bar is never clearer than a KPI tile.
        m = measures[0]
        return {"type": "kpi_tile", "x": None, "y": [m["name"]], "title": title_hint or m["label"]}

    if times and 1 <= len(measures) <= 3:
        return {"type": "line", "x": times[0]["name"], "y": [m["name"] for m in measures], "title": title_hint or "Trend over time"}

    if len(dims) == 1 and len(measures) == 1 and row_count <= 25:
        m = measures[0]
        chart_type = "stacked100" if m["unit"] == "%" else "bar"
        return {"type": chart_type, "x": dims[0]["name"], "y": [m["name"]], "title": title_hint or m["label"]}

    if len(dims) == 1 and len(measures) == 2 and measures[0]["unit"] != measures[1]["unit"]:
        return {"type": "bar_dot", "x": dims[0]["name"], "y": [m["name"] for m in measures], "title": title_hint or "Two measures compared"}

    if not dims and not times and len(measures) == 2 and row_count > 10:
        return {"type": "scatter", "x": measures[0]["name"], "y": [measures[1]["name"]], "title": title_hint or "Two measures compared"}

    return {"type": "table", "x": None, "y": [], "title": title_hint or "Full result"}


def valid_types_for_shape(columns: list[dict], row_count: int) -> list[str]:
    """Every chart type that would be a legitimate alternative view for this exact shape (for the frontend's
    type switcher, which must never offer a type code did not validate)."""
    chosen = choose_chart(columns, row_count)
    out = [chosen["type"]]
    dims = [c for c in columns if c["role"] == "dimension"]
    measures = [c for c in columns if c["role"] == "measure"]
    if chosen["type"] in ("bar", "stacked100") and len(dims) == 1 and len(measures) == 1:
        out.append("table")
    if chosen["type"] == "line":
        out.append("table")
    if chosen["type"] not in out:
        out.append("table")
    # dedupe, preserve order
    seen = set()
    return [t for t in out if not (t in seen or seen.add(t))]
