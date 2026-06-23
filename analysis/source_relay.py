import pandas as pd


def compute_source_relay_summary(df, min_departures=1000):
    totals = df.groupby('Origin').size()
    inherited = df.groupby('Origin')['LateAircraftDelay'].apply(lambda x: (x > 0).sum())

    summary = pd.DataFrame({
        'total_departures': totals,
        'inherited_count': inherited,
    })
    summary['originated_count'] = summary['total_departures'] - summary['inherited_count']
    summary['inheritance_rate'] = summary['inherited_count'] / summary['total_departures']

    return summary[summary['total_departures'] >= min_departures].sort_values(
        'inheritance_rate', ascending=False
    )