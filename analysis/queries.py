
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

