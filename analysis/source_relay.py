import pandas as pd


def compute_source_relay_summary(df):
    totals = df.groupby('Origin', observed=True).size()
    inherited = df.groupby('Origin', observed=True)['LateAircraftDelay'].apply(lambda x: (x > 0).sum())

    summary = pd.DataFrame({
        'total_departures': totals,
        'inherited_count': inherited,
    })
    summary['originated_count'] = summary['total_departures'] - summary['inherited_count']
    summary['inheritance_rate'] = summary['inherited_count'] / summary['total_departures']
    summary['is_reliable'] = summary['total_departures'] >= 1000

    return summary.sort_values('inheritance_rate', ascending=False)