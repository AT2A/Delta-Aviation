import pandas as pd


def summarize_cascades_by_airport(cascade_events):
    events_df = pd.DataFrame(cascade_events)
    grouped = events_df.groupby('origin_airport').agg(
        cascade_count=('depth', 'count'),
        avg_cascade_depth=('depth', 'mean'),
        max_cascade_depth=('depth', 'max'),
        total_inherited_minutes=('total_delay_inherited', 'sum'),
    )
    return grouped


def build_reconciliation_table(source_relay_df, betweenness, weighted_betweenness, degree_centrality, cascade_events):
    cascade_summary = summarize_cascades_by_airport(cascade_events)

    table = source_relay_df.copy()
    table['betweenness'] = pd.Series(betweenness)
    table['weighted_betweenness'] = pd.Series(weighted_betweenness)
    table['degree_centrality'] = pd.Series(degree_centrality)
    table['is_zero_betweenness'] = table['betweenness'] == 0.0
    table['is_zero_weighted_betweenness'] = table['weighted_betweenness'] == 0.0
    table = table.join(cascade_summary, how='left')

    table['rank_inheritance'] = table['inheritance_rate'].rank(ascending=False)
    table['rank_betweenness'] = table['betweenness'].rank(ascending=False)
    table['rank_weighted_betweenness'] = table['weighted_betweenness'].rank(ascending=False)
    table['rank_degree'] = table['degree_centrality'].rank(ascending=False)

    table['betweenness_vs_inheritance_gap'] = table['rank_betweenness'] - table['rank_inheritance']

    return table