import pickle
from pathlib import Path

import pandas as pd
from pipeline import run_full_reconciliation
from centrality import build_weighted_graph, compute_betweenness_centrality

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

with open(DATA_DIR / "delta_rotation_graph.pkl", "rb") as f:
    G = pickle.load(f)

df = pd.read_csv(DATA_DIR / "delta_ontime_with_datetimes.csv", low_memory=False)

table = run_full_reconciliation(df, G)

print(table.index.name)
print(type(table.index))
print(table['betweenness'].isna().sum(), "out of", len(table))

print("\nZero-betweenness airports:", table['is_zero_betweenness'].sum())
print("Zero-weighted-betweenness airports:", table['is_zero_weighted_betweenness'].sum())

print("\nATL row:")
print(table.loc['ATL', ['betweenness', 'weighted_betweenness', 'is_zero_betweenness', 'is_zero_weighted_betweenness']])

print("\nBOS row:")
print(table.loc['BOS', ['betweenness', 'weighted_betweenness', 'is_zero_betweenness', 'is_zero_weighted_betweenness']])

print("\nGap, excluding zero-betweenness ties:")
non_zero = table[~table['is_zero_betweenness']]
print(non_zero.sort_values('betweenness_vs_inheritance_gap', ascending=False)[
    ['betweenness', 'inheritance_rate', 'rank_betweenness', 'rank_inheritance', 'betweenness_vs_inheritance_gap']
].head(10))