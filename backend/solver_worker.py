import pickle
from pathlib import Path

from analysis.queries import compute_route_delay_summary, build_flight_frame_from_graph
from analysis.centrality import build_weighted_graph, compute_betweenness_centrality
from analysis.disruption import (
    assign_recovery_segments,
    assign_recovery_segments_optimal,
    _build_route_duration_table,
)

DATA_DIR = Path(__file__).parent.parent / "data"

_worker_state = {}


def init_worker():
    """ProcessPoolExecutor initializer -- runs once per worker process.

    Windows uses the 'spawn' start method (no fork, no shared memory with
    the main process), so each worker independently rebuilds the tables the
    solver functions need.

    Measured directly (not assumed) that passing legs_by_tail through
    ProcessPoolExecutor's initargs instead is NOT faster: it's a ~449MB
    structure of ~1.27M small per-leg dicts, and pickling/unpickling that
    many individual Python objects (~50s round trip, measured) costs about
    as much as just reloading the graph pickle here and rebuilding it
    locally (~19.5s load + ~1.6s build, measured) -- so this rebuilds
    in-process rather than receiving it via initargs. What IS a real,
    measured win: building the (Origin, Dest, ArrDelay, Cancelled,
    Diverted, LateAircraftDelay) frame from already-loaded graph edge
    attributes (build_flight_frame_from_graph, ~2s) instead of re-parsing
    the 1.27M-row CSV from disk (~13.6s) -- that data is redundant with
    what's already on the graph, see analysis/queries.py.
    """
    with open(DATA_DIR / "delta_rotation_graph.pkl", "rb") as f:
        G = pickle.load(f)

    legs_by_tail = {}
    for u, v, d in G.edges(data=True):
        legs_by_tail.setdefault(d['tail_number'], []).append((u, v, d))
    for tail in legs_by_tail:
        legs_by_tail[tail].sort(key=lambda e: e[2]['crs_dep_dt'])

    df = build_flight_frame_from_graph(G)
    route_table = compute_route_delay_summary(df)

    G_weighted = build_weighted_graph(G)
    centrality_table = compute_betweenness_centrality(G_weighted)

    airport_coords = {
        code: (data['lat'], data['lon'])
        for code, data in G.nodes(data=True)
        if data.get('lat') is not None and data.get('lon') is not None
    }

    _worker_state['legs_by_tail'] = legs_by_tail
    _worker_state['route_table'] = route_table
    _worker_state['centrality_table'] = centrality_table
    _worker_state['airport_coords'] = airport_coords
    _worker_state['route_duration_table'] = _build_route_duration_table(legs_by_tail)


def solve(tail_number, date, time, mode, solver):
    solver_fn = assign_recovery_segments if solver == "greedy" else assign_recovery_segments_optimal
    return solver_fn(
        tail_number,
        date,
        time,
        _worker_state['legs_by_tail'],
        _worker_state['route_table'],
        _worker_state['centrality_table'],
        _worker_state['airport_coords'],
        mode=mode,
        route_duration_table=_worker_state['route_duration_table'],
    )
