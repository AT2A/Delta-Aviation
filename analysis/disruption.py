import pandas as pd
from analysis.queries import get_aircraft_state


def get_downstream_legs(legs_by_tail, tail_number, date, origin, destination):
    all_legs = legs_by_tail.get(tail_number, [])
    day_legs = [leg for leg in all_legs if leg[2]['flight_date'] == date]

    cancelled_index = None
    for i, (u, v, d) in enumerate(day_legs):
        if u == origin and v == destination:
            cancelled_index = i
            break

    if cancelled_index is None:
        return None, []

    cancelled_leg = day_legs[cancelled_index]
    downstream = day_legs[cancelled_index + 1:]
    return cancelled_leg, downstream


def find_swap_candidates(legs_by_tail, origin, date, query_time, max_candidates=5):
    query_dt = pd.to_datetime(f"{date} {query_time}")
    candidates = []

    for tail, legs in legs_by_tail.items():
        day = [leg for leg in legs if leg[2]['flight_date'] == date]
        if not day:
            continue
        aircraft_state = get_aircraft_state(day, query_dt)
        if aircraft_state['status'] == 'parked' and aircraft_state['destination'] == origin:
            candidates.append(tail)
        if len(candidates) >= max_candidates:
            break

    return candidates