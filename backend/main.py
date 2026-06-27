from fastapi import FastAPI, HTTPException
from pathlib import Path
import pickle
import pandas
import pandas as pd
from analysis.pipeline import run_full_reconciliation
from analysis.queries import compute_route_delay_summary, compute_tail_summary, get_tail_chain
from pydantic import BaseModel
from typing import List

    

DATA_DIR = Path(__file__).parent.parent / "data"

app = FastAPI()

state = {}

class Airport(BaseModel):
    Origin: str
    total_departures: int
    inheritance_rate: float
    betweenness: float
    weighted_betweenness: float
    is_zero_betweenness: bool
class Route(BaseModel):
    Origin: str
    Dest: str
    avg_delay: float
    flight_count: int
    cancelled_count: int
    total_flights: int
    cancellation_rate: float
    diverted_count: int
class TailSummary(BaseModel):
    tail_number: str
    total_legs: int


@app.on_event("startup")
def load_data():
    with open(DATA_DIR / "delta_rotation_graph.pkl", "rb") as f:
        state["G"] = pickle.load(f)
    state["df"] = pandas.read_csv(DATA_DIR / "delta_ontime_with_datetimes.csv", low_memory=False)
    print("Graph loaded:", state["G"].number_of_nodes(), "nodes")
    print("Dataframe loaded:", len(state["df"]), "rows")
    state['airport_table'] = run_full_reconciliation(state['df'], state["G"])
    state['route_table'] = compute_route_delay_summary(state['df'])
    state['tail_summary'] = compute_tail_summary(state['G'])


@app.get("/debug/node-count")
def node_count():
    return {"nodes": state["G"].number_of_nodes()}


class AirportsResponse(BaseModel):
    airports: List[Airport]

@app.get("/airports", response_model=AirportsResponse)
def get_airports():
    columns = ['total_departures', 'inheritance_rate', 'betweenness', 'weighted_betweenness', 'is_zero_betweenness']
    trimmed = state["airport_table"][columns].reset_index()
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