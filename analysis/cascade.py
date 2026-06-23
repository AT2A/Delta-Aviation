DELAY_THRESHOLD = 15


def starts_cascade(leg_data):
    arr_delay = leg_data['arr_delay']
    late_aircraft_delay = leg_data['late_aircraft_delay']
    if arr_delay >= DELAY_THRESHOLD and not (late_aircraft_delay > 0):
        return True
    return False


def continues_cascade(leg_data):
    late_aircraft_delay = leg_data['late_aircraft_delay']
    if late_aircraft_delay > 0:
        return True
    return False


def trace_cascades(chain_legs):
    """
    chain_legs: list of (u, v, data) tuples for ONE tail number,
                already sorted chronologically.

    Returns a list of event dicts, one per cascade found in this chain.
    """
    events = []
    in_cascade = False
    cascade_start_i = None
    cascade_delay_total = 0.0

    n_legs = len(chain_legs)

    for i in range(n_legs):
        u, v, d = chain_legs[i]

        if not in_cascade:
            if starts_cascade(d):
                in_cascade = True
                cascade_start_i = i
                cascade_delay_total = 0.0

        else:
            if continues_cascade(d):
                cascade_delay_total += d['late_aircraft_delay']
                continue

            start_u, _, _ = chain_legs[cascade_start_i]
            events.append({
                'tail_number': d['tail_number'],
                'start_leg_index': cascade_start_i,
                'end_leg_index': i - 1,
                'depth': (i - 1) - cascade_start_i + 1,
                'total_delay_inherited': cascade_delay_total,
                'origin_airport': start_u,
            })

            in_cascade = False
            cascade_start_i = None
            cascade_delay_total = 0.0

            if starts_cascade(d):
                in_cascade = True
                cascade_start_i = i
                cascade_delay_total = 0.0

    if in_cascade:
        end_i = n_legs - 1
        start_u, _, _ = chain_legs[cascade_start_i]
        events.append({
            'tail_number': chain_legs[end_i][2]['tail_number'],
            'start_leg_index': cascade_start_i,
            'end_leg_index': end_i,
            'depth': end_i - cascade_start_i + 1,
            'total_delay_inherited': cascade_delay_total,
            'origin_airport': start_u,
        })

    return events

def trace_all_cascades(G):
    legs_by_tail = {}
    for u, v, d in G.edges(data=True):
        legs_by_tail.setdefault(d['tail_number'], []).append((u, v, d))

    all_events = []
    for tail, legs in legs_by_tail.items():
        legs.sort(key=lambda e: e[2]['crs_dep_dt'])
        all_events.extend(trace_cascades(legs))

    return all_events