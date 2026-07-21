from .source_relay import compute_source_relay_summary
from .centrality import build_weighted_graph, compute_betweenness_centrality, compute_weighted_betweenness, compute_degree_centrality
from .cascade import trace_cascades
from .reconcile import build_reconciliation_table

def run_full_reconciliation(df, G, G_weighted=None, btw=None, legs_by_tail=None):
    summary = compute_source_relay_summary(df)
    if G_weighted is None:
        G_weighted = build_weighted_graph(G)
    if btw is None:
        btw = compute_betweenness_centrality(G_weighted)
    wbtw = compute_weighted_betweenness(G_weighted)
    deg = compute_degree_centrality(G_weighted)

    if legs_by_tail is None:
        legs_by_tail = {}
        for u, v, d in G.edges(data=True):
            legs_by_tail.setdefault(d['tail_number'], []).append((u, v, d))
        for tail in legs_by_tail:
            legs_by_tail[tail].sort(key=lambda e: e[2]['crs_dep_dt'])

    all_events = []
    for tail, legs in legs_by_tail.items():
        all_events.extend(trace_cascades(legs))

    return build_reconciliation_table(summary, btw, wbtw, deg, all_events)