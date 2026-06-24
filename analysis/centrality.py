import networkx as nx


def build_weighted_graph(G):

    route_counts = {}

    for u, v, d in G.edges(data=True):
        key = (u, v)
        route_counts[key] = route_counts.get(key, 0) + 1

    G_weighted = nx.DiGraph()
    G_weighted.add_nodes_from(G.nodes(data=True))  # keep node attributes (e.g. city)

    for (u, v), count in route_counts.items():
        G_weighted.add_edge(u, v, weight=count)

    return G_weighted


def compute_degree_centrality(G_weighted):
    n = G_weighted.number_of_nodes()
    return {
        node: (G_weighted.in_degree(node) + G_weighted.out_degree(node)) / (2 * (n - 1))
        for node in G_weighted.nodes()
    }


def compute_weighted_degree(G_weighted):
    return dict(G_weighted.degree(weight='weight'))


def compute_betweenness_centrality(G_weighted):
    return nx.betweenness_centrality(G_weighted)


def compute_weighted_betweenness(G_weighted):
    return nx.betweenness_centrality(G_weighted, weight=lambda u, v, d: 1 / d['weight'])