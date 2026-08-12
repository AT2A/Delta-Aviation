"""
Loaders for the lean artifacts produced by build_lean_artifacts.py
(data/legs_frame.pkl, data/airport_nodes.pkl), used in place of unpickling
the full graph (data/delta_rotation_graph.pkl) in any live process.
"""
import pickle
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent.parent / "data"
LEGS_FRAME_PATH = DATA_DIR / "legs_frame.pkl"
AIRPORT_NODES_PATH = DATA_DIR / "airport_nodes.pkl"


def load_legs_frame():
    return pd.read_pickle(LEGS_FRAME_PATH)


def load_airport_nodes():
    with open(AIRPORT_NODES_PATH, "rb") as f:
        return pickle.load(f)


def build_flight_frame(legs_frame):
    """Origin/Dest/ArrDelay/Cancelled/Diverted/LateAircraftDelay projection
    used for route/network-level aggregates -- the DataFrame equivalent of
    the old build_flight_frame_from_graph(G).
    """
    return legs_frame.rename(columns={
        "arr_delay": "ArrDelay",
        "cancelled": "Cancelled",
        "diverted": "Diverted",
        "late_aircraft_delay": "LateAircraftDelay",
    })[["Origin", "Dest", "ArrDelay", "Cancelled", "Diverted", "LateAircraftDelay"]]


class LegRow:
    """Dict-like view onto one row of a legs_frame, read lazily from each
    column's own backing array instead of being copied out into a real
    dict. analysis/disruption.py does d['key'] / d.get('key') on ~1.27M of
    these -- materializing each as an actual dict (to_dict('records'))
    measured ~2.3GB of real RSS, since a 15-19 key Python dict costs far
    more than the typed columnar storage it was copied from. This costs two
    pointers per leg instead.

    Indexes each column's pandas ExtensionArray (Series.array) directly,
    not DataFrame.iat -- .iat's per-call block-manager overhead is fine for
    the handful of fields a single request touches, but find_swap_candidates
    (analysis/disruption.py) scans every leg of every tail per /disrupt
    request (a pre-existing pattern, not something this rewrite added), and
    at ~1.27M .iat calls that request never came back in an interactive
    amount of time. Column-array indexing measured ~30-60x faster per call.
    """
    __slots__ = ('_columns', '_pos')

    def __init__(self, columns, pos):
        self._columns = columns
        self._pos = pos

    def __getitem__(self, key):
        return self._columns[key][self._pos]

    def get(self, key, default=None):
        col = self._columns.get(key)
        if col is None:
            return default
        return col[self._pos]


def build_legs_by_tail(legs_frame):
    """dict[tail_number -> list[(Origin, Dest, leg_dict)]], sorted by
    scheduled departure -- the same shape analysis/disruption.py and
    analysis/queries.py expect from graph edges, without needing the graph.

    A plain linear scan with setdefault, not groupby -- legs_frame is
    already sorted by (tail_number, crs_dep_dt) (build_lean_artifacts.py),
    so a single pass preserves per-tail order without groupby's
    hashing/grouping overhead.
    """
    # Each column's own ExtensionArray -- Categorical.__getitem__ returns
    # the label string, DatetimeArray.__getitem__ returns a Timestamp (not
    # numpy.datetime64, which lacks .strftime()), shared read-only across
    # every LegRow rather than copied per leg.
    columns = {
        col: legs_frame[col].array for col in legs_frame.columns
        if col not in ("Origin", "Dest")
    }
    tails = legs_frame["tail_number"].tolist()
    origins = legs_frame["Origin"].tolist()
    dests = legs_frame["Dest"].tolist()

    legs_by_tail = {}
    for pos in range(len(legs_frame)):
        legs_by_tail.setdefault(tails[pos], []).append(
            (origins[pos], dests[pos], LegRow(columns, pos))
        )
    return legs_by_tail


def build_legs_by_tail_and_date(legs_by_tail):
    """dict[(tail_number, flight_date) -> list[(Origin, Dest, leg_dict)]],
    regrouping the same leg tuples build_legs_by_tail already built (not
    rebuilding LegRow objects) by day. main.py's /state endpoint needs one
    tail's legs for one date; filtering each tail's entire flight history
    per request touches every leg in the dataset on every call, and /state
    is polled about once a second during live replay -- this index turns
    that into a direct dict lookup instead.
    """
    index = {}
    for tail, legs in legs_by_tail.items():
        for leg in legs:
            index.setdefault((tail, leg[2]['flight_date']), []).append(leg)
    return index


def build_route_duration_table(legs_frame):
    """Vectorized equivalent of analysis.disruption._build_route_duration_table
    (average scheduled duration in minutes per (Origin, Dest) route), computed
    directly from legs_frame. That function does 2 dict-style accesses per
    leg, which is fine for the handful of legs touched per request but not
    for all 1.27M at once -- measured ~68s through LegRow vs a fraction of a
    second here.
    """
    valid = legs_frame.dropna(subset=["crs_dep_dt", "crs_arr_dt"])
    minutes = (valid["crs_arr_dt"] - valid["crs_dep_dt"]).dt.total_seconds() / 60
    avg = minutes.groupby([valid["Origin"], valid["Dest"]], observed=True).mean()
    return {key: float(value) for key, value in avg.items()}


def build_departure_index(legs_frame):
    """Vectorized equivalent of analysis.disruption._build_departure_index
    ({(airport, date): sorted departure times}), computed directly from
    legs_frame for the same reason as build_route_duration_table.

    Values are numpy datetime64 arrays, not lists of boxed Timestamp
    objects (~173MB vs ~22MB for this dataset) -- bisect_left/bisect_right
    (the only consumer, analysis.disruption.connecting_flights_missed) work
    identically against a numpy array, verified against arbitrary Timestamp
    probes, not just values already in the data.
    """
    grouped = legs_frame.groupby(["Origin", "flight_date"], observed=True)["crs_dep_dt"]
    return {key: times.sort_values().to_numpy() for key, times in grouped}
