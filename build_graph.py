import pandas as pd
import networkx as nx
import pickle

INPUT_PATH = 'data/delta_ontime_with_datetimes.csv'
GRAPH_OUTPUT_PATH = 'data/delta_rotation_graph.pkl'

#Thresholds for turnaround
FRAGILE_THRESHOLD_MIN = 45
OVERNIGHT_THRESHOLD_MIN = 180

NEEDED_COLS = [
    'Tail_Number', 'FlightDate', 'Origin', 'Dest', 'OriginCityName', 'DestCityName',
    'CRSDepTime', 'DepTime', 'CRSArrTime', 'ArrTime',
    'CRSDepDatetime', 'CRSArrDatetime', 'DepDatetime', 'ArrDatetime',
    'WheelsOffDatetime', 'WheelsOnDatetime',
    'DepDelay', 'ArrDelay', 'LateAircraftDelay',
    'Cancelled', 'Diverted', 'Distance',
    'TaxiOut', 'TaxiIn', 'WheelsOff', 'WheelsOn',
]

def load_data(path):
    df = pd.read_csv(path, usecols=NEEDED_COLS, low_memory=False)

    #Parse Datetime
    for col in ['CRSDepDatetime', 'CRSArrDatetime', 'DepDatetime', 'ArrDatetime', 'WheelsOffDatetime', 'WheelsOnDatetime']:
        df[col] = pd.to_datetime(df[col], errors='coerce')
    
    #Drop row with no tail num
    n_before = len(df)
    df = df.dropna(subset=['Tail_Number'])
    print(f'  Dropped {n_before - len(df):,} rows with null Tail_Number')
    
    #Sort tail number in chronological order
    df = df.sort_values(['Tail_Number', 'CRSDepDatetime']).reset_index(drop=True)
    return df


def build_rotation_chains(df):
    chains = {
        tail:group.reset_index(drop=True)
        for tail, group in df.groupby('Tail_Number')
    }
    print(f'  {len(chains):,} unique tail numbers')
    return chains

def classify_turnaround(turnaround_minutes):
    if turnaround_minutes is None or pd.isna(turnaround_minutes):
        return None, None
    if turnaround_minutes < 0:
        return 'invalid_or_ferry_gap', None
    if turnaround_minutes > OVERNIGHT_THRESHOLD_MIN:
        return 'overnight_or_long_rest', False
    return 'gate_turn', turnaround_minutes < FRAGILE_THRESHOLD_MIN

def add_airport_nodes(G, df):
    origin_meta=df[['Origin', 'OriginCityName']].rename(
        columns={'Origin':'code', 'OriginCityName': 'city'})
   
    dest_meta = df[['Dest', 'DestCityName']].rename(
        columns={'Dest': 'code', 'DestCityName': 'city'})
    
    all_airports = pd.concat([origin_meta, dest_meta]).drop_duplicates(subset='code')
    
    for _, row, in all_airports.iterrows():
        G.add_node(row['code'], city =row['city'])
        
    print(f'  {G.number_of_nodes():,} airport nodes added')
    
def build_graph(chains, df):
    G = nx.MultiDiGraph()
    add_airport_nodes(G, df)
    
    n_edges = 0
    n_fragile = 0
    n_ferry_gaps = 0
    
    for tail, chain in chains.items():
        n_legs = len(chain)
        
        for i in range(n_legs):
            row = chain.iloc[i]
             
            #compute turnaround to NEXT leg
            turnaround_minutes= None
            turn_type = None
            is_fragile = None
            
            if i < n_legs - 1:
                next_row = chain.iloc[i + 1]
            
                if next_row['Origin'] == row['Dest']:
                    if pd.notna(row['CRSArrDatetime']) and pd.notna(next_row['CRSDepDatetime']):
                        delta = next_row['CRSDepDatetime'] - row['CRSArrDatetime']
                        turnaround_minutes = delta.total_seconds() / 60
                        turn_type, is_fragile = classify_turnaround(turnaround_minutes)
                else: 
                    turn_type = 'chain_discontinuity'
                    n_ferry_gaps += 1

            if turn_type == 'gate_turn' and is_fragile:
                n_fragile += 1
            
            G.add_edge(
                row['Origin'], row['Dest'],
                tail_number=tail,
                flight_date=row['FlightDate'],
                crs_dep_dt=row['CRSDepDatetime'],
                crs_arr_dt=row['CRSArrDatetime'],
                dep_dt=row['DepDatetime'],
                arr_dt=row['ArrDatetime'],
                dep_delay=row['DepDelay'],
                arr_delay=row['ArrDelay'],
                late_aircraft_delay=row['LateAircraftDelay'],
                cancelled=row['Cancelled'],
                diverted=row['Diverted'],
                distance=row['Distance'],
                turnaround_to_next=turnaround_minutes,
                turn_type=turn_type,
                is_fragile=is_fragile,
                taxi_out=row['TaxiOut'],
                taxi_in=row['TaxiIn'],
                wheels_off=row['WheelsOffDatetime'],
                wheels_on=row['WheelsOnDatetime'],
            )
            n_edges += 1
    print(f'  {n_edges:,} flight edges added')
    print(f'  {n_fragile:,} fragile gate turns (< {FRAGILE_THRESHOLD_MIN} min buffer)')
    print(f'  {n_ferry_gaps:,} chain discontinuities (likely ferry/positioning flights)')
    return G

def validate_graph(G, chains):
    
    print('\nValidation:')
    print(f'  Nodes (airports): {G.number_of_nodes():,}')
    print(f'  Edges (flights):  {G.number_of_edges():,}')
    
    sample_tail = next(iter(chains))
    sample_edges = [
        (u, v, d) for u, v, d in G.edges(data=True)
        if d['tail_number'] == sample_tail
    ]
    print(f'\n  Sample rotation for tail {sample_tail} ({len(sample_edges)} legs):')
    for u, v, d in sorted(sample_edges, key=lambda e: e[2]['crs_dep_dt']):
        turn = f"{d['turnaround_to_next']:.0f} min ({d['turn_type']})" if d['turnaround_to_next'] is not None else "—"
        print(f'    {u} -> {v}  |  dep_delay={d["dep_delay"]}  late_aircraft_delay={d["late_aircraft_delay"]}  next_turn={turn}')
        
        

def main():
    df = load_data(INPUT_PATH)
 
    print('\nBuilding rotation chains')
    chains = build_rotation_chains(df)
 
    print('\nBuilding graph')
    G = build_graph(chains, df)
 
    validate_graph(G, chains)
 
    print(f'\nSaving graph to {GRAPH_OUTPUT_PATH} ')
    with open(GRAPH_OUTPUT_PATH, 'wb') as f:
        pickle.dump(G, f)
    print('Done.')
 
 
if __name__ == '__main__':
    main()