"""Middle-mile data prep: checkpoint rows -> legs -> trips (time fields are cumulative within a leg)."""
from __future__ import annotations

import os

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw", "delhivery_data.csv")
PROCESSED = os.path.join(ROOT, "data", "processed")

TRAIN_END = pd.Timestamp("2018-09-26 23:59:59")
TEST_START = pd.Timestamp("2018-09-27")
VAL_START = pd.Timestamp("2018-09-21")

NEVER_FEATURES = ["actual_time", "segment_actual_time", "actual_distance_to_destination", "factor", "segment_factor",
                  "start_scan_to_end_scan", "od_end_time", "cutoff_factor", "cutoff_timestamp", "is_cutoff"]

_STATE = r"\(([^)]+)\)\s*$"


def state_of(name: pd.Series) -> pd.Series:
    """'City_Place_X (State)' -> 'State'."""
    return name.astype(str).str.extract(_STATE, expand=False).fillna("UNKNOWN")


def load_raw() -> pd.DataFrame:
    df = pd.read_csv(RAW)
    for c in ("trip_creation_time", "od_start_time", "od_end_time"):
        df[c] = pd.to_datetime(df[c])
    return df


def build_legs(df: pd.DataFrame) -> pd.DataFrame:
    """One row per (trip_uuid, source_center, destination_center). Cumulative fields -> max; constant fields -> first."""
    keys = ["trip_uuid", "source_center", "destination_center"]
    g = df.groupby(keys, sort=False)
    legs = g.agg(
        actual_time=("actual_time", "max"), osrm_time=("osrm_time", "max"), osrm_distance=("osrm_distance", "max"),
        start_scan_to_end_scan=("start_scan_to_end_scan", "first"),
        od_start_time=("od_start_time", "first"), od_end_time=("od_end_time", "first"),
        source_name=("source_name", "first"), destination_name=("destination_name", "first"),
        route_type=("route_type", "first"), route_schedule_uuid=("route_schedule_uuid", "first"),
        trip_creation_time=("trip_creation_time", "first"),
    ).reset_index()
    legs = legs.sort_values(["trip_uuid", "od_start_time", "od_end_time"]).reset_index(drop=True)
    legs["leg_position"] = legs.groupby("trip_uuid").cumcount() + 1
    legs["legs_in_trip"] = legs.groupby("trip_uuid")["trip_uuid"].transform("size")
    legs["source_state"] = state_of(legs["source_name"])
    legs["destination_state"] = state_of(legs["destination_name"])
    return legs


def build_trips(legs: pd.DataFrame) -> pd.DataFrame:
    """One row per trip: leg values summed; first source / last destination; corridor = first source state -> last destination state."""
    g = legs.groupby("trip_uuid", sort=False)
    first, last = g.head(1).set_index("trip_uuid"), g.tail(1).set_index("trip_uuid")
    trips = pd.DataFrame({
        "actual_min": g["actual_time"].sum(), "osrm_time_total": g["osrm_time"].sum(), "osrm_distance_total": g["osrm_distance"].sum(),
        "n_legs": g.size(), "route_type": first["route_type"], "route_schedule_uuid": first["route_schedule_uuid"],
        "trip_creation_time": first["trip_creation_time"],
        "first_source_center": first["source_center"], "last_destination_center": last["destination_center"],
        "first_source_state": first["source_state"], "last_destination_state": last["destination_state"],
    }).reset_index()
    trips["corridor"] = trips["first_source_state"] + " -> " + trips["last_destination_state"]
    trips["creation_hour"] = trips["trip_creation_time"].dt.hour
    trips["creation_weekday"] = trips["trip_creation_time"].dt.weekday
    trips["ratio"] = trips["actual_min"] / trips["osrm_time_total"].where(trips["osrm_time_total"] > 0)
    trips["train"] = trips["trip_creation_time"] <= TRAIN_END
    trips["test"] = trips["trip_creation_time"] >= TEST_START
    trips["val"] = trips["train"] & (trips["trip_creation_time"] >= VAL_START)
    return trips
