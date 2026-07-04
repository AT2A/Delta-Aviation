
def compute_route_delay_summary(df):
    grouped = df.groupby(['Origin', 'Dest']).agg(
        avg_delay=('ArrDelay', 'mean'),
        flight_count=('ArrDelay', 'count'),
        cancelled_count=('Cancelled', 'sum'),
        diverted_count=('Diverted', 'sum'),
        total_flights=('Cancelled', 'count'),
    )
    grouped['cancellation_rate'] = grouped['cancelled_count'] / grouped['total_flights']
    return grouped

def compute_tail_summary(G):
    tail_counts = {}
    for u, v, d in G.edges(data=True):
        tail_counts[d['tail_number']] = tail_counts.get(d['tail_number'], 0) + 1
 
    tails = [{"tail_number": tail, "total_legs": count} for tail, count in tail_counts.items()]
    tails.sort(key=lambda t: t['total_legs'], reverse=True)
    return tails

def get_tail_chain(G, tail_number, date=None):
    legs = sorted(
        [(u, v, d) for u, v, d in G.edges(data=True)
         if d['tail_number'] == tail_number and (date is None or d['flight_date'] == date)],
        key=lambda e: e[2]['crs_dep_dt']
    )
    return legs

def find_owning_leg(legs, query_datetime):
    owning_leg = None
    for leg in legs:
        u, v, d = leg
        if d['dep_dt'] is not None and d['dep_dt'] <= query_datetime:
            owning_leg = leg
        else:
            break
    return owning_leg
 
 
def classify_aircraft_status(leg_data, query_datetime):
    if leg_data['cancelled'] == 1.0:
        return 'cancelled'

    dep = leg_data['dep_dt']
    wheels_off = leg_data['wheels_off']
    wheels_on = leg_data['wheels_on']
    arr = leg_data['arr_dt']

    if arr is None:
        return 'in_flight'

    if wheels_off is not None and query_datetime < wheels_off:
        return 'taxiing_out'

    if wheels_on is not None and query_datetime < wheels_on:
        return 'in_flight'

    if query_datetime < arr:
        return 'taxiing_in'

    return 'parked'
 
 
def get_aircraft_state(legs, query_datetime):
    if not legs:
        return {
            'status': 'not_operating',
            'origin': None,
            'destination': None,
            'wheels_off': None,
            'wheels_on': None,
        }
    owning_leg = find_owning_leg(legs, query_datetime)
    if owning_leg is None:
        return {
            'status': 'not_yet_started',
            'origin': legs[0][0],
            'destination': None,
            'wheels_off': None,
            'wheels_on': None,
        }
    u, v, d = owning_leg
    status = classify_aircraft_status(d, query_datetime)
    return {
        'status': status,
        'origin': u,
        'destination': v,
        'wheels_off': d['wheels_off'],
        'wheels_on': d['wheels_on'],
    }