from pathlib import Path

from analysis.queries import compute_route_delay_summary
from analysis.centrality import build_weighted_graph_from_frame, compute_betweenness_centrality
from analysis.data_loading import (
    load_legs_frame, load_airport_nodes, build_legs_by_tail, build_flight_frame,
    build_route_duration_table,
)
from analysis.disruption import (
    assign_recovery_segments,
    assign_recovery_segments_optimal,
)

DATA_DIR = Path(__file__).parent.parent / "data"

_worker_state = {}


def init_worker():
    """Worker-process initializer. Windows' 'spawn' start method shares no
    memory with the main process, so each worker rebuilds its own tables.

    Loads the lean legs_frame/airport_nodes artifacts (build_lean_artifacts.py)
    rather than the full per-flight graph pickle -- unpickling that graph
    alone measured ~4.4GB of real RSS per process, and every solver worker
    paid that cost independently, making the app undeployable on any
    reasonably sized host.
    """
    legs_frame = load_legs_frame()
    airport_nodes = load_airport_nodes()

    legs_by_tail = build_legs_by_tail(legs_frame)

    df = build_flight_frame(legs_frame)
    route_table = compute_route_delay_summary(df)

    G_weighted = build_weighted_graph_from_frame(legs_frame, airport_nodes)
    centrality_table = compute_betweenness_centrality(G_weighted)

    airport_coords = {
        code: (attrs['lat'], attrs['lon'])
        for code, attrs in airport_nodes.items()
        if attrs.get('lat') is not None and attrs.get('lon') is not None
    }

    _worker_state['legs_by_tail'] = legs_by_tail
    _worker_state['route_table'] = route_table
    _worker_state['centrality_table'] = centrality_table
    _worker_state['airport_coords'] = airport_coords
    _worker_state['route_duration_table'] = build_route_duration_table(legs_frame)


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
