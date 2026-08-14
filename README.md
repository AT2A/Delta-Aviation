# Aircraft Rotation & Delay Propagation Network

An end-to-end analysis and simulation platform for Delta Air Lines' domestic
network, built on ~1.27M real flights (January 2025 – April 2026, BTS
TranStats On-Time Performance data). It answers two connected questions:

1. **How does delay actually propagate through an airline's network?**
   (network analysis, Phase 1–2)
2. **When a flight is disrupted, what's the best way to recover, and how
   confident should we be in that recommendation?**
   (interactive disruption simulator, Phase 3+)

The project is split across three live pages: a network overview, a live
replay + disruption simulator, and a propagation-analysis page presenting the
core research findings.

---

## Why this project

This project traces delay to its source, distinguishing delay that
originates at a given flight from delay it inherited from an earlier,
connected flight. **28.5% of all delay-minutes in the network are inherited,
not originated.**

It also builds a real recommendation engine for disruption recovery, not a
black-box score: every design choice (weights, proxies, validation approach)
is stated explicitly, stress-tested against synthetic edge cases, and
benchmarked against a provably optimal solver.

---

## Data

**Source:** BTS TranStats On-Time Performance data. The raw dataset covers
all reporting carriers; this project trims it down to Delta domestic
flights only, January 2025 – April 2026 (~1.27M flights, ~1,015 unique tail
numbers).

**Airport coordinates:** OpenFlights `airports.dat`, joined by IATA code,
covering all 153 airports that appear as nodes in the graph. OpenFlights
data is licensed under the [Open Database License (ODbL)](https://www.openflights.org/data.php).

Each flight record carries scheduled and actual departure/arrival times,
tail number, origin, destination, and `LateAircraftDelay` — the field used
to detect inherited (vs. originated) delay and trace cascades through a
tail number's rotation.

This is historical, batch-loaded data, not a live feed. It's precomputed
into a graph once at startup and served from static tables (see
Architecture below).

---

## Architecture

**Backend** — Python, FastAPI (`backend/main.py`)
On startup, `load_data()` loads a precomputed flight-rotation graph (153
airports, ~1.27M edges) and builds the tables the API needs: route
statistics, betweenness centrality, per-tail flight sequences, airport
coordinates, and route durations. A 2-worker process pool handles the two
disruption solvers so CPU-bound optimization work never blocks the main
request-serving process.

**Analysis layer** — Python, NetworkX, pandas (`analysis/`)
Pure, side-effect-free functions with no API dependencies, covering graph
construction, cascade tracing, centrality analysis, and disruption
simulation. Each module has its own manual validation block (`if __name__ ==
"__main__":`) with hand-traced examples checked against real flights, not
just unit tests against synthetic data.

**Frontend** — React (Vite), deck.gl
Three pages, sharing a common design system (Inter + JetBrains Mono, a
consistent card-based light theme, dark mode support):
- **Network Overview** (`/`) — the static network map and headline research
  findings
- **Live Replay & Disruption Simulator** (`/replay`) — a time-scrubbing
  replay of a real day's operations, with click-to-disrupt any flight or
  aircraft
- **Propagation Analysis** (`/analysis`) — the core Phase 2 research findings,
  visualized

Routes render as true great-circle paths (not deck.gl's default stylized 3D
arcs), and in-flight aircraft are positioned by real spherical interpolation
between wheels-off and wheels-on, not snapped to origin or destination.

---

## Core findings

### The network absorbs disruption better than intuition suggests

**Betweenness centrality (structural importance) and inheritance rate
(realized cascade risk) measure different things.** That gap is the core
finding in this project.

- **ATL** dominates the network's structural centrality (weighted betweenness
  ≈ 0.93) but ranks only 73rd of 153 airports by inheritance rate:
  structurally critical, but well-buffered against cascading delay.
- **Florida/leisure airports** (PBI, FLL, MIA, TPA, MCO) show the opposite
  pattern: near-zero structural centrality, but among the highest inheritance
  rates in the network. High-traffic endpoints that disproportionately relay
  delay, despite being structurally peripheral.

High traffic does not imply high centrality, and high centrality does not
imply high realized cascade risk. Naive intuition ("busy airport = risky
airport") is wrong in both directions.

### Most single-flight disruptions are non-events, structurally

Across a 100-scenario diagnostic sample, **74% of single-leg cancellations
resolve to a clean, zero-delay recovery or need no recovery at all**, 24%
find no viable substitute at all, and only 2% show a genuine, nuanced
tradeoff between candidates. This was tested three separate ways (varying
the query-time convention, requiring recent-landing candidates, excluding
idle candidates entirely) and held every time: each variant moved only ~1%
of scenarios into a harder outcome bucket relative to baseline. **The
driver is schedule buffer baked into Delta's real timetable, not candidate
availability.** This confirms the ATL finding above independently — the
network absorbs small shocks without needing to reroute much of anything.

### Mass disruption is a supply problem, not a coordination problem

Simulating an 80% cancellation of a real, busy airport's morning departures
found that **most orphaned flights have zero viable substitutes at all**
(67% at the busiest morning window, still 41% at the best-supplied midday
window), not because of poor allocation, but because there simply isn't
enough spare capacity. Genuine scheduling conflicts (the same aircraft
being the best option for two different flights) were real but secondary,
affecting only 7–11% of successfully-recovered legs. The gap between the
morning and midday windows is itself informative: scarcity eases
substantially once more of the fleet is in rotation, so the morning number
should be read as a stress case, not a fixed worst-case bound.

### The optimal solver earns its complexity

A greedy, chronological assignment algorithm was benchmarked against a
provably optimal (memoized dynamic programming) solver across real
whole-aircraft-down scenarios. The optimal solver found real, quantified
improvements: **13% lower total disruption cost** in one tested scenario,
by seeing the whole assignment problem at once instead of committing greedy,
myopic choices leg-by-leg.

---

## The disruption recovery engine

### Feature 1 — Single-leg cancellation

Given one cancelled flight, the engine finds every physically viable
substitute aircraft and scores each one on a weighted combination of:
- **Induced delay** — how late the substitute makes this specific flight
- **Opportunity cost** — the real cost of pulling that aircraft away from
  its own remaining schedule (including the time it actually spends flying
  the substitute assignment, and repositioning back)
- **Importance** — a proxy for how disruptive it is to touch this route at
  all, built from real traffic volume and structural centrality, weighted
  toward centrality as a deliberate callback to the project's own Phase 2
  finding

Five ranking modes let the user choose what to optimize for (minimize this
flight's delay, minimize total network delay, protect other flights, protect
major flights, or a balanced combination), implemented as one formula with
five weight vectors, not five separate algorithms, so every mode remains
aware of every factor.

### Feature 2 — Whole-aircraft-down

When an entire aircraft is grounded, every remaining flight on its rotation
becomes orphaned simultaneously, and no single substitute may be able to
cover all of them. The engine assigns contiguous *segments* of the
remaining schedule to different substitute aircraft, deciding at each step
whether a candidate should extend to cover more flights or hand off to a
fresh search: the same tradeoff logic as Feature 1, applied recursively.

Two solvers are available:
- **Greedy** — fast, resolves segments in order
- **Optimal** — a memoized dynamic-programming search over the full
  assignment space, guaranteed to find the best possible outcome

Both run in isolated worker processes with a timeout-and-fallback mechanism,
so a slow optimal solve never blocks other users' requests.

---

## Validation philosophy

There is no public dataset of "the correct disruption recovery decision":
airlines don't publish their real dispatch/recovery actions. So this project
doesn't claim to have found the objectively correct answer; it validates
what *can* be validated:

- **Monotonicity** — does the score always improve when any individual
  factor improves, holding everything else constant?
- **Pareto-dominance** — does the ranking ever recommend a candidate that's
  strictly worse than another available candidate on every factor? (It
  never does, by construction, across all five ranking modes.)
- **Weight sensitivity** — how much does the top recommendation change under
  different, reasonable weightings? (Tested explicitly, reported honestly
  either way.)
- **Cross-validation between independently built components** — Feature 1
  and Feature 2 compute overlapping quantities differently, and a real
  disagreement between them exposed a genuine bug, not just a design
  difference: fixing it shifted the zero-cost rate from 95.2% to 41.9%.

---

## Known limitations

Stated explicitly rather than silently worked around:

- **No crew, maintenance, or gate/slot data exists in BTS data.** The
  recovery engine cannot model crew duty-time limits, maintenance holds, or
  gate constraints. A real substitute recommendation may not be
  operationally legal in ways this system can't see.
- **BTS data structurally excludes ferry/positioning flights.** An aircraft
  that's actually in active use repositioning between airports can appear
  as "idle" in this dataset — a real, unresolved gap, corroborated by an
  idle-fleet rate roughly 2x higher than Delta's real reported utilization.
- **No passenger-level data exists.** "Importance" and "connections missed"
  are both traffic/schedule-based proxies, not real passenger counts,
  explicitly labeled as such wherever they appear.
- **No historical record of Delta's actual recovery decisions exists** in
  any public dataset, which is why this project validates its methodology
  for internal consistency rather than against real-world outcomes.

---

## Running locally

The backend needs two data artifacts at startup — `data/legs_frame.pkl` and
`data/airport_nodes.pkl` (~163MB combined) — that aren't in this repo. Get
them one of two ways:

**Quick start — download the prebuilt artifacts (recommended)**

Download `legs_frame.pkl` and `airport_nodes.pkl` from the
[latest release](https://github.com/AT2A/Delta-Aviation/releases/latest) and
place both in `data/`.

**From scratch — rebuild the pipeline yourself**

1. Get the BTS TranStats On-Time Performance data (Reporting Carrier
   On-Time Performance dataset,
   [transtats.bts.gov](https://www.transtats.bts.gov/Fields.asp?gnoyr_VQ=FGJ)),
   January 2025 – April 2026. BTS's download flow is per-month and manual;
   there's no scripted bulk fetch. Filter to Delta domestic flights and save
   as `data/delta_ontime_clean.csv` — this filtering/cleaning step is
   currently done by hand and isn't captured in a tracked script.
2. Download [OpenFlights `airports.dat`](https://github.com/jpatokal/openflights/blob/master/data/airports.dat)
   into `data/airports.dat`. OpenFlights data is licensed under the
   [Open Database License (ODbL)](https://www.openflights.org/data.php).
3. Run the pipeline in order, each reading the previous step's output:
   ```bash
   python fix_datetimes.py       # delta_ontime_clean.csv -> delta_ontime_with_datetimes.csv
   python build_graph.py         # -> delta_rotation_graph.pkl
   python add_coordinates.py     # joins airport coordinates onto the graph
   python build_lean_artifacts.py  # -> data/legs_frame.pkl, data/airport_nodes.pkl
   ```

**Backend:**
```bash
pip install -r requirements.txt
uvicorn backend.main:app --reload
```

**Frontend:**
```bash
cd frontend
npm install
npm run dev
```

The frontend expects the backend running on `localhost:8000` (proxied via
Vite's dev server config).