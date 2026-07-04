from fastapi import FastAPI, HTTPException
from pathlib import Path
import pickle
import pandas
import pandas as pd
from analysis.pipeline import run_full_reconciliation
from analysis.queries import compute_route_delay_summary, compute_tail_summary, get_tail_chain, get_aircraft_state
from analysis.disruption import get_downstream_legs, find_swap_candidates
from analysis.geo import great_circle_interpolate, great_circle_path
from pydantic import BaseModel
from typing import List, Optional
from enum import Enum
from fastapi.middleware.cors import CORSMiddleware

DATA_DIR = Path(__file__).parent.parent / "data"

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

@app.on_event("startup")
def load_data():
    with open(DATA_DIR / "delta_rotation_graph.pkl", "rb") as f:
        state["G"] = pickle.load(f)
    state["df"] = pandas.read_csv(DATA_DIR / "delta_ontime_with_datetimes.csv", low_memory=False)
    print("Graph loaded:", state["G"].number_of_nodes(), "nodes")
    print("Dataframe loaded:", len(state["df"]), "rows")
    state['airport_table'] = run_full_reconciliation(state['df'], state["G"])
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

    state['tail_summary'] = compute_tail_summary(state['G'])
    legs_by_tail = {}
    for u, v, d in state["G"].edges(data=True):
        legs_by_tail.setdefault(d['tail_number'], []).append((u, v, d))
    for tail in legs_by_tail:
        legs_by_tail[tail].sort(key=lambda e: e[2]['crs_dep_dt'])
    state['legs_by_tail'] = legs_by_tail

@app.get("/debug/node-count")
def node_count():
    return {"nodes": state["G"].number_of_nodes()}


class AirportsResponse(BaseModel):
    airports: List[Airport]

@app.get("/airports", response_model=AirportsResponse)
def get_airports():
    columns = ['total_departures', 'inheritance_rate', 'betweenness', 'weighted_betweenness', 'is_zero_betweenness', 'is_reliable']
    trimmed = state["airport_table"][columns].reset_index()

    lats = {code: data['lat'] for code, data in state["G"].nodes(data=True)}
    lons = {code: data['lon'] for code, data in state["G"].nodes(data=True)}
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

    legs = get_tail_chain(state["G"], tail_number, date)

    if not legs:
        return {"legs": [], "message": f"{tail_number} had no flights on {date}"}

    return {"legs": [shape_leg(u, v, d) for u, v, d in legs]}

@app.get("/tail/{tail_number}")
def get_tail_full_history(tail_number: str):
    known_tails = {t["tail_number"] for t in state["tail_summary"]}

    if tail_number not in known_tails:
        raise HTTPException(status_code=404, detail=f"Tail number {tail_number} not found")

    legs = get_tail_chain(state["G"], tail_number)

    return {"legs": [shape_leg(u, v, d) for u, v, d in legs]}


class StateResponse(BaseModel):
    aircraft: List[AircraftState]

# Statuses where the aircraft hasn't left `origin` yet -- position it there.
# taxiing_in/parked position at `destination`; in_flight is interpolated separately.
ORIGIN_STATUSES = {"not_yet_started", "taxiing_out", "cancelled"}

@app.get("/state", response_model=StateResponse)
def get_state(date: str, time: str):
    query_datetime = pd.to_datetime(f"{date} {time}")

    lats = {code: data['lat'] for code, data in state["G"].nodes(data=True)}
    lons = {code: data['lon'] for code, data in state["G"].nodes(data=True)}

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


class DisruptResponse(BaseModel):
    tail_number: str
    cancelled_leg: str
    downstream_legs: List[str]
    # STUB metric: sum of pre-existing historical arr_delay on downstream legs.
    # Not a simulated cascade -- identical regardless of whether this leg is
    # actually cancelled. Real induced-delay propagation via turnaround_to_next
    # is Day 11 scope, shared with swap-ranking logic.
    total_cascade_minutes: float
    swap_candidates: List[str]
    
@app.post("/disrupt", response_model=DisruptResponse)
def disrupt_flight(request: DisruptRequest):
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

    # STUB: this is NOT a simulated cascade -- it sums pre-existing historical
    # arr_delay on downstream legs, which is identical whether or not this leg
    # (or any leg resolving to the same cancelled_index) is actually cancelled.
    # Real simulation (propagating induced delay through turnaround_to_next)

    total_cascade_minutes = sum(
        d['arr_delay'] for _, _, d in downstream
        if d['arr_delay'] is not None and not pd.isna(d['arr_delay']) and d['arr_delay'] > 0
    )

    swap_candidates = find_swap_candidates(
        state['legs_by_tail'],
        request.origin,
        request.date,
        request.time,
    )

    return {
        "tail_number": request.tail_number,
        "cancelled_leg": f"{request.origin}->{request.destination}",
        "downstream_legs": downstream_legs,
        "total_cascade_minutes": total_cascade_minutes,
        "swap_candidates": swap_candidates,
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
    
    # count departures per airport from the graph nodes
    airport_departures = {}
    for u, v, d in state["G"].edges(data=True):
        airport_departures[u] = airport_departures.get(u, 0) + 1

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