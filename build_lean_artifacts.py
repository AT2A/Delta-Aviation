"""
Derives lean, deployable data artifacts from the full graph pickle.

data/delta_rotation_graph.pkl (a NetworkX MultiDiGraph with one edge per
individual flight, ~1.27M edges) costs ~4.4GB of real RSS to unpickle --
measured directly, not estimated from file size -- because each edge's
attribute dict and NetworkX's own adjacency scaffolding both carry heavy
per-object Python overhead at this row count. Nothing in the live-serving
path (analysis/disruption.py, the /state endpoint, etc.) actually needs the
graph object itself after startup; they consume plain dicts/DataFrames
derived from it once. This script produces those derived artifacts directly
so the live process never has to hold the full graph in memory at all.

Run this after build_graph.py + add_coordinates.py, whenever the graph
pickle changes.
"""
import pickle
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent / "data"
GRAPH_PATH = DATA_DIR / "delta_rotation_graph.pkl"
LEGS_OUTPUT_PATH = DATA_DIR / "legs_frame.pkl"
NODES_OUTPUT_PATH = DATA_DIR / "airport_nodes.pkl"

# Matches the kwargs build_graph.py passes to G.add_edge(), minus Origin/Dest
# (the edge endpoints, captured separately as u/v).
EDGE_ATTR_COLS = [
    'tail_number', 'flight_date', 'crs_dep_dt', 'crs_arr_dt', 'dep_dt', 'arr_dt',
    'dep_delay', 'arr_delay', 'late_aircraft_delay', 'cancelled', 'diverted',
    'distance', 'turnaround_to_next', 'turn_type', 'is_fragile',
    'taxi_out', 'taxi_in', 'wheels_off', 'wheels_on',
]
CATEGORY_COLS = ['Origin', 'Dest', 'tail_number', 'flight_date']
DATETIME_COLS = ['crs_dep_dt', 'crs_arr_dt', 'dep_dt', 'arr_dt', 'wheels_off', 'wheels_on']
FLOAT_COLS = [
    'dep_delay', 'arr_delay', 'late_aircraft_delay', 'cancelled', 'diverted',
    'distance', 'turnaround_to_next', 'taxi_out', 'taxi_in',
]
# turn_type and is_fragile are left as plain object dtype (not category /
# nullable-boolean). Both can be a real Python None (missing turnaround
# data), and turn_type is echoed verbatim in API responses -- category
# dtype's missing-value sentinel is float NaN, not None, and NaN isn't
# valid JSON (None correctly serializes to null). Pandas' nullable boolean
# dtype has the same problem for is_fragile: bool(pd.NA) raises, which
# would break any plain truthiness check on the field.


def build_legs_frame(G):
    rows = []
    for u, v, d in G.edges(data=True):
        row = {'Origin': u, 'Dest': v}
        row.update({col: d.get(col) for col in EDGE_ATTR_COLS})
        rows.append(row)
    df = pd.DataFrame(rows)

    for col in DATETIME_COLS:
        df[col] = pd.to_datetime(df[col])
    for col in FLOAT_COLS:
        df[col] = df[col].astype('float64')
    for col in CATEGORY_COLS:
        df[col] = df[col].astype('category')

    return df.sort_values(['tail_number', 'crs_dep_dt']).reset_index(drop=True)


def build_airport_nodes(G):
    return {
        code: {'city': data.get('city'), 'lat': data.get('lat'), 'lon': data.get('lon')}
        for code, data in G.nodes(data=True)
    }


def main():
    print(f"Loading {GRAPH_PATH} ...")
    with open(GRAPH_PATH, 'rb') as f:
        G = pickle.load(f)
    print(f"  {G.number_of_nodes():,} nodes, {G.number_of_edges():,} edges")

    print("Flattening edges into a typed DataFrame ...")
    legs_frame = build_legs_frame(G)
    mem_mb = legs_frame.memory_usage(deep=True).sum() / 1e6
    print(f"  legs_frame: {len(legs_frame):,} rows, {mem_mb:.1f} MB in memory")
    legs_frame.to_pickle(LEGS_OUTPUT_PATH)
    print(f"  saved to {LEGS_OUTPUT_PATH}")

    print("Extracting airport node metadata ...")
    airport_nodes = build_airport_nodes(G)
    with open(NODES_OUTPUT_PATH, 'wb') as f:
        pickle.dump(airport_nodes, f)
    print(f"  {len(airport_nodes)} airport nodes saved to {NODES_OUTPUT_PATH}")

    print("Done.")


if __name__ == '__main__':
    main()
