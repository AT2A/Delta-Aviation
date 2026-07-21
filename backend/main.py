from fastapi import FastAPI, HTTPException
from pathlib import Path
import pickle
import pandas as pd
import asyncio
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from analysis.pipeline import run_full_reconciliation
from analysis.queries import compute_route_delay_summary, compute_tail_summary, get_aircraft_state, build_flight_frame_from_graph
from analysis.disruption import get_downstream_legs, find_swap_candidates, rank_candidates, compute_recovery_improvement, RANKING_MODES, _build_route_duration_table
from analysis.centrality import build_weighted_graph, compute_betweenness_centrality
from analysis.geo import great_circle_interpolate, great_circle_path
from backend.solver_worker import solve as solve_aircraft_down, init_worker
from pydantic import BaseModel
from typing import List, Optional, Tuple
from enum import Enum
from fastapi.middleware.cors import CORSMiddleware

DATA_DIR = Path(__file__).parent.parent / "data"
OPTIMAL_SOLVER_TIMEOUT_SECONDS = 20

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

state = {}

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
class DisruptRequest(BaseModel):
    tail_number: str
    date: str
    time: str
    origin: str
    destination: str
    mode: str = "all_factors_combined"

@app.on_event("startup")
def load_data():
    with open(DATA_DIR / "delta_rotation_graph.pkl", "rb") as f:
        state["G"] = pickle.load(f)
    state["df"] = build_flight_frame_from_graph(state["G"])
    print("Graph loaded:", state["G"].number_of_nodes(), "nodes")
    print("Flight frame built from graph:", len(state["df"]), "rows")

    legs_by_tail = {}
    airport_departures = {}
    for u, v, d in state["G"].edges(data=True):
        legs_by_tail.setdefault(d['tail_number'], []).append((u, v, d))
        airport_departures[u] = airport_departures.get(u, 0) + 1
    for tail in legs_by_tail:
        legs_by_tail[tail].sort(key=lambda e: e[2]['crs_dep_dt'])
    state['airport_departures'] = airport_departures

    G_weighted = build_weighted_graph(state["G"])
    btw = compute_betweenness_centrality(G_weighted)
    state['centrality_table'] = btw

    state['airport_table'] = run_full_reconciliation(
        state['df'], state["G"], G_weighted=G_weighted, btw=btw, legs_by_tail=legs_by_tail,
    )
    state['route_table'] = compute_route_delay_summary(state['df'])

    lats = {code: data.get('lat') for code, data in state["G"].nodes(data=True)}
    lons = {code: data.get('lon') for code, data in state["G"].nodes(data=True)}

    def route_path(row):
        o_lat, o_lon = lats.get(row['Origin']), lons.get(row['Origin'])
        d_lat, d_lon = lats.get(row['Dest']), lons.get(row['Dest'])
        if None in (o_lat, o_lon, d_lat, d_lon):
            return None
        return great_circle_path(o_lat, o_lon, d_lat, d_lon)

    routes_with_path = state['route_table'].reset_index()
    routes_with_path['path'] = routes_with_path.apply(route_path, axis=1)
    state['route_table'] = routes_with_path.set_index(['Origin', 'Dest'])

    state['tail_summary'] = compute_tail_summary(legs_by_tail)
    state['legs_by_tail'] = legs_by_tail

    state['airport_coords'] = {
        code: (data['lat'], data['lon'])
        for code, data in state["G"].nodes(data=True)
        if data.get('lat') is not None and data.get('lon') is not None
    }
    state['route_duration_table'] = _build_route_duration_table(legs_by_tail)

    state['solver_pool'] = ProcessPoolExecutor(max_workers=2, initializer=init_worker)
    state['solver_semaphore'] = asyncio.Semaphore(2)


@app.on_event("shutdown")
def shutdown_solver_pool():
    state['solver_pool'].shutdown(wait=False)


@app.get("/debug/node-count")
def node_count():
    return {"nodes": state["G"].number_of_nodes()}


class AirportsResponse(BaseModel):
    airports: List[Airport]

@app.get("/airports", response_model=AirportsResponse)
def get_airports():
    columns = ['total_departures', 'inheritance_rate', 'betweenness', 'weighted_betweenness', 'is_zero_betweenness', 'is_reliable']
    trimmed = state["airport_table"][columns].reset_index()

    lats = {code: coord[0] for code, coord in state['airport_coords'].items()}
    lons = {code: coord[1] for code, coord in state['airport_coords'].items()}
    trimmed['lat'] = trimmed['Origin'].map(lats)
    trimmed['lon'] = trimmed['Origin'].map(lons)

    return {"airports": trimmed.to_dict(orient='records')}


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
        all_legs = state['legs_by_tail'].get(tail_number, [])
        legs = [leg for leg in all_legs if leg[2]['flight_date'] == date]
        aircraft_state = get_aircraft_state(legs, query_datetime)

        origin_lat, origin_lon = lats.get(aircraft_state['origin']), lons.get(aircraft_state['origin'])
        dest_lat, dest_lon = lats.get(aircraft_state['destination']), lons.get(aircraft_state['destination'])

        lat = lon = None
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
        })

    return {"aircraft": results}


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

    # Candidates must be parked at the cancelled leg's DESTINATION (where the
    # downstream chain begins), not its origin -- fixes a pre-existing bug
    # where this call passed request.origin instead.
    candidates = find_swap_candidates(
        state['legs_by_tail'],
        request.destination,
        request.date,
        request.time,
    )

    ranked = rank_candidates(
        cancelled_leg, downstream, candidates, request.mode,
        state['route_table'], state['centrality_table'], state['legs_by_tail'],
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

    loop = asyncio.get_running_loop()
    pool = state['solver_pool']
    fn = partial(solve_aircraft_down, request.tail_number, request.date, request.time, request.mode, request.solver)

    optimal_timed_out = False
    async with semaphore:
        if request.solver == "optimal":
            try:
                result = await asyncio.wait_for(loop.run_in_executor(pool, fn), timeout=OPTIMAL_SOLVER_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                fallback_fn = partial(solve_aircraft_down, request.tail_number, request.date, request.time, request.mode, "greedy")
                result = await loop.run_in_executor(pool, fallback_fn)
                optimal_timed_out = True
        else:
            result = await loop.run_in_executor(pool, fn)

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
    tail_counts = {t["tail_number"]: t["total_legs"] for t in state["tail_summary"]}
    airport_departures = state['airport_departures']

    result = []
    for code, data in state["G"].nodes(data=True):
        if data.get("lat") is None:
            continue
        result.append({
            "Origin": code,
            "lat": data["lat"],
            "lon": data["lon"],
            "total_legs": airport_departures.get(code, 0),
        })

    return {"airports": result}