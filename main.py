import pandas as pd
import networkx as nx
import matplotlib.pyplot as plt

# Load only the columns you need
df = pd.read_csv('data/delta_ontime_clean.csv', usecols=['Origin', 'Dest', 'Flights', 'Distance', 'ArrDelay', 'Cancelled','Tail_Number'])
print(df['Tail_Number'].nunique())

# Aggregate by route (Origin → Dest)
routes = df.groupby(['Origin', 'Dest']).agg(
    flight_count  = ('Flights',  'sum'),
    avg_delay     = ('ArrDelay', 'mean'),
    total_cancel  = ('Cancelled','sum'),
    distance      = ('Distance', 'mean')
).reset_index()

# Pull what we need, including time and date fields
df = pd.read_csv('data/delta_ontime_clean.csv', usecols=[
    'Tail_Number', 'FlightDate', 'Origin', 'Dest', 
    'CRSDepTime', 'DepTime', 'CRSArrTime', 'ArrTime',
    'Flights', 'Cancelled', 'ArrDelay', 'DepDelay'
])

# Sort chronologically within each aircraft so the chain reads in order
df = df.sort_values(['Tail_Number', 'FlightDate', 'CRSDepTime'])

# Dictionary: each key is one aircraft, value is its full flight sequence
aircraft_chains = {
    tail: group.reset_index(drop=True)
    for tail, group in df.groupby('Tail_Number')
}

# Example: look at one aircraft's full day-by-day route history
example_tail = df['Tail_Number'].dropna().iloc[0]
print(aircraft_chains[example_tail][['FlightDate','Origin','Dest','CRSDepTime','CRSArrTime']].head(15))
