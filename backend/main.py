from fastapi import FastAPI, HTTPException
from pathlib import Path
import os
import pandas as pd
import asyncio
from functools import partial
from analysis.pipeline import run_full_reconciliation
from analysis.queries import compute_route_delay_summary, compute_tail_summary, get_aircraft_state
from analysis.disruption import (
    get_downstream_legs, find_swap_candidates, rank_candidates, compute_recovery_improvement,
    RANKING_MODES, _day_filtered_legs_by_tail,
)
from analysis.centrality import build_weighted_graph_from_frame, compute_betweenness_centrality
from analysis.data_loading import (
    load_legs_frame, load_airport_nodes, build_legs_by_tail, build_flight_frame,
    build_route_duration_table, build_departure_index, build_legs_by_tail_and_date,
)
from analysis.geo import great_circle_interpolate, great_circle_path
from backend.solver_worker import solve as solve_aircraft_down, init_worker
from backend.worker_pool import KillableWorkerPool
from pydantic import BaseModel
from typing import List, Optional, Tuple
from enum import Enum
from fastapi.middleware.cors import CORSMiddleware

DATA_DIR = Path(__file__).parent.parent / "data"
OPTIMAL_SOLVER_TIMEOUT_SECONDS = 20
NUM_SOLVER_WORKERS = int(os.environ.get("NUM_SOLVER_WORKERS", 2))   # single source of truth for both pool size and the concurrency cap below

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

state = {}

def _parse_state(city):
    if not city or "," not in city:
        return None
    return city.rsplit(",", 1)[-1].strip()

class Airport(BaseModel):
    Origin: str
    total_departures: int
    inheritance_rate: float
    betweenness: float
    weighted_betweenness: float
    is_zero_betweenness: bool
    is_reliable: bool
    lat: float
    lon: float
    city: Optional[str] = None
    state: Optional[str] = None
class Route(BaseModel):
    Origin: str
    Dest: str
    avg_delay: float
    flight_count: int
    cancelled_count: int
    total_flights: int
    cancellation_rate: float
    diverted_count: int
    path: Optional[List[List[float]]] = None
class TailSummary(BaseModel):
    tail_number: str
    total_legs: int
    
class AircraftStatus(str, Enum):
    not_operating = "not_operating"
    not_yet_started = "not_yet_started"
    taxiing_out = "taxiing_out"
    in_flight = "in_flight"
    taxiing_in = "taxiing_in"
    parked = "parked"
    cancelled = "cancelled"

class AircraftState(BaseModel):
    tail_number: str
    status: AircraftStatus
    origin: Optional[str] = None
    destination: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    # Set only for in_flight aircraft with valid timing, so the frontend can
    # interpolate position between /state polls.
    wheels_off: Optional[str] = None
    wheels_on: Optional[str] = None
    origin_lat: Optional[float] = None
    origin_lon: Optional[float] = None
    dest_lat: Optional[float] = None
    dest_lon: Optional[float] = None
class DisruptRequest(BaseModel):
    tail_number: str
    date: str
    time: str
    origin: str
    destination: str
    mode: str = "all_factors_combined"

@app.on_event("startup")
def load_data():
    legs_frame = load_legs_frame()
    airport_nodes = load_airport_nodes()
    state["airport_nodes"] = airport_nodes
    state["airport_city"] = {code: attrs.get("city") for code, attrs in airport_nodes.items()}
    state["df"] = build_flight_frame(legs_frame)
    print("Airport nodes loaded:", len(airport_nodes))
    print("Flight frame loaded:", len(state["df"]), "rows")

    df = state["df"]
    delayed_minutes = df["ArrDelay"].fillna(0).clip(lower=0).sum()
    inherited_minutes = df["LateAircraftDelay"].fillna(0).sum()
    state["network_inherited_delay_pct"] = round(inherited_minutes / delayed_minutes * 100, 1) if delayed_minutes > 0 else 0.0

    legs_by_tail = build_legs_by_tail(legs_frame)
    state['airport_departures'] = {
        code: int(count) for code, count in legs_frame['Origin'].value_counts().items()
    }

    G_weighted = build_weighted_graph_from_frame(legs_frame, airport_nodes)
    btw = compute_betweenness_centrality(G_weighted)
    state['centrality_table'] = btw

    state['airport_table'] = run_full_reconciliation(
        state['df'], None, G_weighted=G_weighted, btw=btw, legs_by_tail=legs_by_tail,
    )

    cascade_counts = state['airport_table']['cascade_count'].fillna(0)
    state['network_total_cascades'] = int(cascade_counts.sum())
    if cascade_counts.sum() > 0:
        state['network_avg_cascade_depth'] = round(
            (state['airport_table']['avg_cascade_depth'].fillna(0) * cascade_counts).sum() / cascade_counts.sum(), 2
        )
    else:
        state['network_avg_cascade_depth'] = 0.0
    state['network_max_cascade_depth'] = int(state['airport_table']['max_cascade_depth'].fillna(0).max())

    state['route_table'] = compute_route_delay_summary(state['df'])

    lats = {code: attrs.get('lat') for code, attrs in airport_nodes.items()}
    lons = {code: attrs.get('lon') for code, attrs in airport_nodes.items()}

    def route_path(row):
        o_lat, o_lon = lats.get(row['Origin']), lons.get(row['Origin'])
        d_lat, d_lon = lats.get(row['Dest']), lons.get(row['Dest'])
        if None in (o_lat, o_lon, d_lat, d_lon):
            return None
        # 8, not the 30-point default: a great-circle route's deviation from
        # a straight chord is one broad, gentle bulge, not a high-frequency
        # wiggle, so a handful of points already traces it with no visible
        # faceting -- verified visually (network-wide view down to dense
        # regional close-ups, on both the longest routes in the network
        # (e.g. JFK-HNL, ~4300nm) and short/medium ones) before trimming
        # this. Cuts /routes' path-data payload by ~3.75x (the dominant
        # cost in a ~35-50MB response for 23,409 routes).
        return great_circle_path(o_lat, o_lon, d_lat, d_lon, num_points=8)

    routes_with_path = state['route_table'].reset_index()
    routes_with_path['path'] = routes_with_path.apply(route_path, axis=1)
    state['route_table'] = routes_with_path.set_index(['Origin', 'Dest'])

    state['tail_summary'] = compute_tail_summary(legs_by_tail)
    state['legs_by_tail'] = legs_by_tail
    state['legs_by_tail_date'] = build_legs_by_tail_and_date(legs_by_tail)

    state['airport_coords'] = {
        code: (attrs['lat'], attrs['lon'])
        for code, attrs in airport_nodes.items()
        if attrs.get('lat') is not None and attrs.get('lon') is not None
    }
    state['route_duration_table'] = build_route_duration_table(legs_frame)
    state['departure_index'] = build_departure_index(legs_frame)

    state['solver_pool'] = KillableWorkerPool(num_workers=NUM_SOLVER_WORKERS, initializer=init_worker)
    state['solver_pool'].start()
    state['solver_semaphore'] = asyncio.Semaphore(NUM_SOLVER_WORKERS)


@app.on_event("shutdown")
def shutdown_solver_pool():
    state['solver_pool'].shutdown()


@app.get("/debug/node-count")
def node_count():
    return {"nodes": len(state["airport_nodes"])}


class AirportsResponse(BaseModel):
    airports: List[Airport]
    network_inherited_delay_pct: float
    network_total_cascades: int
    network_avg_cascade_depth: float
    network_max_cascade_depth: int

@app.get("/airports", response_model=AirportsResponse)
def get_airports():
    columns = ['total_departures', 'inheritance_rate', 'betweenness', 'weighted_betweenness', 'is_zero_betweenness', 'is_reliable']
    trimmed = state["airport_table"][columns].reset_index()

    lats = {code: coord[0] for code, coord in state['airport_coords'].items()}
    lons = {code: coord[1] for code, coord in state['airport_coords'].items()}
    trimmed['lat'] = trimmed['Origin'].map(lats)
    trimmed['lon'] = trimmed['Origin'].map(lons)
    trimmed['city'] = trimmed['Origin'].map(state['airport_city'])
    trimmed['state'] = trimmed['city'].map(_parse_state)

    return {
        "airports": trimmed.to_dict(orient='records'),
        "network_inherited_delay_pct": state["network_inherited_delay_pct"],
        "network_total_cascades": state["network_total_cascades"],
        "network_avg_cascade_depth": state["network_avg_cascade_depth"],
        "network_max_cascade_depth": state["network_max_cascade_depth"],
    }


class RoutesResponse(BaseModel):
    routes: List[Route]
    
@app.get("/routes", response_model=RoutesResponse)
def get_routes():
    return {"routes": state["route_table"].reset_index().to_dict(orient='records')}


class TailsResponse(BaseModel):
    tails: List[TailSummary]

@app.get("/tails", response_model=TailsResponse)
def get_all_tails():
    return {"tails": state["tail_summary"]}


def shape_leg(u, v, data):
    def clean(value):
        if pd.isna(value):
            return None
        return value

    return {
        "origin": u,
        "dest": v,
        "tail_number": data["tail_number"],
        "flight_date": data["flight_date"],
        "scheduled_departure": str(clean(data["crs_dep_dt"])) if clean(data["crs_dep_dt"]) is not None else None,
        "scheduled_arrival": str(clean(data["crs_arr_dt"])) if clean(data["crs_arr_dt"]) is not None else None,
        "actual_departure": str(clean(data["dep_dt"])) if clean(data["dep_dt"]) is not None else None,
        "actual_arrival": str(clean(data["arr_dt"])) if clean(data["arr_dt"]) is not None else None,
        "dep_delay": clean(data["dep_delay"]),
        "arr_delay": clean(data["arr_delay"]),
        "late_aircraft_delay": clean(data["late_aircraft_delay"]),
        "turnaround_to_next": clean(data["turnaround_to_next"]),
        "turn_type": data["turn_type"],
        "is_fragile": data["is_fragile"],
    }

@app.get("/tail/{tail_number}/{date}")
def get_tail_rotation(tail_number: str, date: str):
    known_tails = {t["tail_number"] for t in state["tail_summary"]}

    if tail_number not in known_tails:
        raise HTTPException(status_code=404, detail=f"Tail number {tail_number} not found")

    legs = [leg for leg in state['legs_by_tail'].get(tail_number, []) if leg[2]['flight_date'] == date]

    if not legs:
        return {"legs": [], "message": f"{tail_number} had no flights on {date}"}

    return {"legs": [shape_leg(u, v, d) for u, v, d in legs]}

@app.get("/tail/{tail_number}")
def get_tail_full_history(tail_number: str):
    known_tails = {t["tail_number"] for t in state["tail_summary"]}

    if tail_number not in known_tails:
        raise HTTPException(status_code=404, detail=f"Tail number {tail_number} not found")

    legs = state['legs_by_tail'].get(tail_number, [])

    return {"legs": [shape_leg(u, v, d) for u, v, d in legs]}


class StateResponse(BaseModel):
    aircraft: List[AircraftState]

# Statuses where the aircraft hasn't left `origin` yet -- position it there.
# taxiing_in/parked position at `destination`; in_flight is interpolated separately.
ORIGIN_STATUSES = {"not_yet_started", "taxiing_out", "cancelled"}

@app.get("/state", response_model=StateResponse)
def get_state(date: str, time: str):
    query_datetime = pd.to_datetime(f"{date} {time}")

    lats = {code: coord[0] for code, coord in state['airport_coords'].items()}
    lons = {code: coord[1] for code, coord in state['airport_coords'].items()}

    results = []
    for t in state["tail_summary"]:
        tail_number = t["tail_number"]
        legs = state['legs_by_tail_date'].get((tail_number, date), [])
        aircraft_state = get_aircraft_state(legs, query_datetime)

        origin_lat, origin_lon = lats.get(aircraft_state['origin']), lons.get(aircraft_state['origin'])
        dest_lat, dest_lon = lats.get(aircraft_state['destination']), lons.get(aircraft_state['destination'])

        lat = lon = None
        wheels_off_iso = wheels_on_iso = None
        interp_origin_lat = interp_origin_lon = interp_dest_lat = interp_dest_lon = None
        if aircraft_state['status'] == 'in_flight':
            wheels_off = aircraft_state['wheels_off']
            wheels_on = aircraft_state['wheels_on']
            has_timing = (
                pd.notna(wheels_off) and pd.notna(wheels_on) and wheels_on > wheels_off
            )
            if has_timing and None not in (origin_lat, origin_lon, dest_lat, dest_lon):
                frac = (query_datetime - wheels_off) / (wheels_on - wheels_off)
                frac = max(0.0, min(1.0, float(frac)))
                lat, lon = great_circle_interpolate(origin_lat, origin_lon, dest_lat, dest_lon, frac)
                # Expose the same inputs so the frontend can redo this interpolation
                # for any time in between, without a fresh /state poll per frame.
                wheels_off_iso = wheels_off.isoformat()
                wheels_on_iso = wheels_on.isoformat()
                interp_origin_lat, interp_origin_lon = origin_lat, origin_lon
                interp_dest_lat, interp_dest_lon = dest_lat, dest_lon
            else:
                # missing timing/coords -- fall back to destination
                lat, lon = dest_lat, dest_lon
        else:
            position_code = (
                aircraft_state['origin']
                if aircraft_state['status'] in ORIGIN_STATUSES
                else aircraft_state['destination']
            )
            lat = lats.get(position_code)
            lon = lons.get(position_code)

        results.append({
            "tail_number": tail_number,
            "status": aircraft_state['status'],
            "origin": aircraft_state['origin'],
            "destination": aircraft_state['destination'],
            "lat": lat,
            "lon": lon,
            "wheels_off": wheels_off_iso,
            "wheels_on": wheels_on_iso,
            "origin_lat": interp_origin_lat,
            "origin_lon": interp_origin_lon,
            "dest_lat": interp_dest_lat,
            "dest_lon": interp_dest_lon,
        })

    return {"aircraft": results}


class ScheduleLeg(BaseModel):
    origin: str
    dest: str
    dep: Optional[str] = None
    wheels_off: Optional[str] = None
    wheels_on: Optional[str] = None
    arr: Optional[str] = None
    cancelled: bool

class TailSchedule(BaseModel):
    tail_number: str
    legs: List[ScheduleLeg]

class ScheduleResponse(BaseModel):
    tails: List[TailSchedule]

@app.get("/schedule", response_model=ScheduleResponse)
def get_schedule(date: str):
    """Every tail's ordered leg list for one date, raw (not resolved to any
    one instant) -- lets the frontend derive status/position for any time
    itself (find_owning_leg/classify_aircraft_status ported to JS) instead
    of polling /state once a second. Tails with no legs that date are
    omitted: /state already returns origin=destination=None (-> lat=lon=None)
    for them, and MapView.jsx's aircraftLayer already filters out null-coord
    aircraft, so they never render as dots either way -- omitting them here
    just shrinks the payload, changes nothing visible.
    """
    def clean(value):
        if pd.isna(value):
            return None
        return value

    def iso(value):
        cleaned = clean(value)
        return cleaned.isoformat() if cleaned is not None else None

    tails = []
    for t in state["tail_summary"]:
        tail_number = t["tail_number"]
        legs = state['legs_by_tail_date'].get((tail_number, date), [])
        if not legs:
            continue
        tails.append({
            "tail_number": tail_number,
            "legs": [
                {
                    "origin": u,
                    "dest": v,
                    "dep": iso(d['dep_dt']),
                    "wheels_off": iso(d['wheels_off']),
                    "wheels_on": iso(d['wheels_on']),
                    "arr": iso(d['arr_dt']),
                    "cancelled": bool(clean(d['cancelled']) == 1.0),
                }
                for u, v, d in legs
            ],
        })

    return {"tails": tails}


class RankedCandidate(BaseModel):
    tail_number: str
    score: float
    delay: float
    network_disruption: float
    connecting_flights_missed: int

class DisruptResponse(BaseModel):
    cancelled_leg: str
    mode: str
    downstream_legs: List[str]
    ranked_candidates: List[RankedCandidate]
    cancellations_avoided: int
    total_induced_delay_minutes: Optional[float]
    baseline_cost_minutes: float
    improvement_pct: Optional[float]
    best_candidate: Optional[str]

# Caches _day_filtered_legs_by_tail's result per date -- /disrupt (single-leg
# cancel) used to call find_swap_candidates directly against the full
# ~1.27M-leg corpus on every request, unlike /disrupt/aircraft-down, which
# already pre-filters by date once per solve (assign_recovery_segments[_optimal],
# analysis/disruption.py). A Live Replay session stays on one date at a time,
# so caching this means only the first /disrupt call for a given date pays
# the full scan -- every later one (a different flight, a different ranking
# mode) reuses the already-filtered per-date view.
_day_filtered_cache = {}

def _get_day_filtered_legs(date):
    cached = _day_filtered_cache.get(date)
    if cached is None:
        cached = _day_filtered_legs_by_tail(state['legs_by_tail'], date)
        _day_filtered_cache[date] = cached
    return cached


@app.post("/disrupt", response_model=DisruptResponse)
def disrupt_flight(request: DisruptRequest):
    if request.mode not in RANKING_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mode '{request.mode}'. Valid options: {list(RANKING_MODES.keys())}"
        )

    cancelled_leg, downstream = get_downstream_legs(
        state['legs_by_tail'],
        request.tail_number,
        request.date,
        request.origin,
        request.destination,
    )

    if cancelled_leg is None:
        raise HTTPException(
            status_code=404,
            detail=f"No leg found from {request.origin} to {request.destination} "
                   f"for {request.tail_number} on {request.date}"
        )

    downstream_legs = [f"{u}->{v}" for u, v, d in downstream]

    # Candidates must be parked at the cancelled leg's destination, where the
    # downstream chain begins -- not its origin.
    candidates = find_swap_candidates(
        _get_day_filtered_legs(request.date),
        request.destination,
        request.date,
        request.time,
    )

    ranked = rank_candidates(
        cancelled_leg, downstream, candidates, request.mode,
        state['route_table'], state['centrality_table'], state['legs_by_tail'],
        route_duration_table=state['route_duration_table'],
        departure_index=state['departure_index'],
    )
    improvement = compute_recovery_improvement(
        cancelled_leg, downstream, candidates, request.mode,
        state['route_table'], state['centrality_table'], state['legs_by_tail'],
        ranked=ranked,
    )

    return {
        "cancelled_leg": f"{request.origin}->{request.destination}",
        "mode": request.mode,
        "downstream_legs": downstream_legs,
        "ranked_candidates": ranked,
        "cancellations_avoided": improvement["cancellations_avoided"],
        "total_induced_delay_minutes": improvement["total_induced_delay_minutes"],
        "baseline_cost_minutes": improvement["baseline_cost_minutes"],
        "improvement_pct": improvement["improvement_pct"],
        "best_candidate": improvement["best_candidate"],
    }


class AircraftDownRequest(BaseModel):
    tail_number: str
    date: str
    time: str
    mode: str = "all_factors_combined"
    solver: str = "greedy"  # "greedy" or "optimal"

class RecoverySegment(BaseModel):
    tail_number: Optional[str]
    legs_covered: List[Tuple[str, str]]
    induced_delay_minutes: Optional[float]
    opportunity_cost: Optional[float]
    return_trip_minutes: Optional[float]
    score: Optional[float]

class AircraftDownResponse(BaseModel):
    tail_number: str
    mode: str
    solver: str
    segments: List[RecoverySegment]
    total_orphaned_legs: int
    total_covered_legs: int
    total_uncovered_legs: int
    num_segments: int
    total_induced_delay_minutes: float
    optimal_timed_out: bool = False

@app.post("/disrupt/aircraft-down", response_model=AircraftDownResponse)
async def aircraft_down(request: AircraftDownRequest):
    if request.mode not in RANKING_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mode '{request.mode}'. Valid options: {list(RANKING_MODES.keys())}"
        )

    if request.solver not in ("greedy", "optimal"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid solver '{request.solver}'. Valid options: ['greedy', 'optimal']"
        )

    if request.tail_number not in state['legs_by_tail']:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown tail number '{request.tail_number}': no flight history found for this aircraft"
        )

    semaphore = state['solver_semaphore']
    if semaphore.locked():
        raise HTTPException(
            status_code=429,
            detail="Solver is busy with other requests, try again shortly"
        )

    pool = state['solver_pool']
    fn = partial(solve_aircraft_down, request.tail_number, request.date, request.time, request.mode, request.solver)

    optimal_timed_out = False
    async with semaphore:
        if request.solver == "optimal":
            try:
                result = await pool.submit(fn, timeout=OPTIMAL_SOLVER_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                fallback_fn = partial(solve_aircraft_down, request.tail_number, request.date, request.time, request.mode, "greedy")
                result = await pool.submit(fallback_fn)
                optimal_timed_out = True
        else:
            result = await pool.submit(fn)

    return {
        "tail_number": request.tail_number,
        "mode": request.mode,
        "solver": "greedy" if optimal_timed_out else request.solver,
        "optimal_timed_out": optimal_timed_out,
        "segments": result["segments"],
        "total_orphaned_legs": result["total_orphaned_legs"],
        "total_covered_legs": result["total_covered_legs"],
        "total_uncovered_legs": result["total_uncovered_legs"],
        "num_segments": result["num_segments"],
        "total_induced_delay_minutes": result["total_induced_delay_minutes"],
    }

class AirportBasic(BaseModel):
    Origin: str
    lat: float
    lon: float
    total_legs: int

class AirportsAllResponse(BaseModel):
    airports: List[AirportBasic]

@app.get("/airports/all", response_model=AirportsAllResponse)
def get_all_airports():
    airport_departures = state['airport_departures']

    result = []
    for code, data in state["airport_nodes"].items():
        if data.get("lat") is None:
            continue
        result.append({
            "Origin": code,
            "lat": data["lat"],
            "lon": data["lon"],
            "total_legs": airport_departures.get(code, 0),
        })

    return {"airports": result}