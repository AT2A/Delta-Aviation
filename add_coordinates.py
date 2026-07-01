import pickle
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent / "data"

airports_df = pd.read_csv(DATA_DIR / "airports.dat", header=None, keep_default_na=False)

coords = {}
for _, row in airports_df.iterrows():
    iata = row[4]
    if iata == "\\N" or iata == "":
        continue
    coords[iata] = (float(row[6]), float(row[7]))

with open(DATA_DIR / "delta_rotation_graph.pkl", "rb") as f:
    G = pickle.load(f)

matched = 0
unmatched = []
for code in G.nodes():
    if code in coords:
        lat, lon = coords[code]
        G.nodes[code]["lat"] = lat
        G.nodes[code]["lon"] = lon
        matched += 1
    else:
        unmatched.append(code)

print(f"Matched: {matched}")
print(f"Unmatched: {len(unmatched)}")
if unmatched:
    print("Unmatched airport codes:", unmatched)

with open(DATA_DIR / "delta_rotation_graph.pkl", "wb") as f:
    pickle.dump(G, f)
