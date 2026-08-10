import pandas as pd
from bisect import bisect_left, bisect_right
from analysis.queries import get_aircraft_state, find_owning_leg
from analysis.geo import great_circle_distance_nm

# Centrality weighted higher than traffic: structural position drives
# network-wide delay propagation more than raw volume (Phase 2 finding).
CENTRALITY_WEIGHT = 0.6
TRAFFIC_WEIGHT = 0.4


def importance_weight(origin, destination, route_table, centrality_table, max_flight_count=None, max_betweenness=None):
    """Proxy for route "importance": combines traffic volume and structural
    centrality. Pass max_flight_count/max_betweenness when calling this
    per-candidate to avoid rescanning route_table/centrality_table each time.

    Returns a float roughly comparable in scale to delay-minutes figures.
    """
    try:
        flight_count = route_table.loc[(origin, destination), 'flight_count']
    except KeyError:
        flight_count = 0  # fallback: no traffic data for this route

    origin_betweenness = centrality_table.get(origin, 0.0)  # fallback: airport missing from centrality data
    destination_betweenness = centrality_table.get(destination, 0.0)  # fallback: same
    max_endpoint_betweenness = max(origin_betweenness, destination_betweenness)

    if max_flight_count is None:
        max_flight_count = route_table['flight_count'].max()
    if max_betweenness is None:
        max_betweenness = max(centrality_table.values())

    normalized_traffic = flight_count / max_flight_count if max_flight_count else 0.0
    normalized_centrality = max_endpoint_betweenness / max_betweenness if max_betweenness else 0.0

    combined = CENTRALITY_WEIGHT * normalized_centrality + TRAFFIC_WEIGHT * normalized_traffic

    # Scaled to roughly match induced_delay_minutes' magnitude.
    return combined * 100


def get_downstream_legs(legs_by_tail, tail_number, date, origin, destination):
    all_legs = legs_by_tail.get(tail_number, [])
    day_legs = [leg for leg in all_legs if leg[2]['flight_date'] == date]

    cancelled_index = None
    for i, (u, v, d) in enumerate(day_legs):
        if u == origin and v == destination:
            cancelled_index = i
            break

    if cancelled_index is None:
        return None, []

    cancelled_leg = day_legs[cancelled_index]
    downstream = day_legs[cancelled_index + 1:]
    return cancelled_leg, downstream


def find_swap_candidates(legs_by_tail, origin, date, query_time):
    """Stage A: which tails can physically cover the cancelled leg (location,
    status, flew-today) -- a feasibility filter, not a quality ranking (see
    rank_candidates). Ferry-flight candidates (parked elsewhere) are excluded
    as a separate future feature.

    The "flew today" check works around classify_aircraft_status reading a
    NaT arrival (diverted, or a data gap with Cancelled == 0) as 'parked' --
    BTS data has no direct flag for long-grounded aircraft, so a tail with no
    completed leg today is excluded outright as a proxy.

    Returns every viable candidate, unranked and uncapped.
    """
    query_dt = pd.to_datetime(f"{date} {query_time}")
    candidates = []

    for tail, legs in legs_by_tail.items():
        day = [leg for leg in legs if leg[2]['flight_date'] == date]
        if not day:
            continue

        flew_today = any(pd.notna(d['dep_dt']) and pd.notna(d['arr_dt']) for u, v, d in day)
        if not flew_today:
            continue

        aircraft_state = get_aircraft_state(day, query_dt)
        if aircraft_state['status'] != 'parked' or aircraft_state['destination'] != origin:
            continue

        owning_leg = find_owning_leg(day, query_dt)
        owning_index = next(i for i, leg in enumerate(day) if leg is owning_leg)
        arr_dt = owning_leg[2]['arr_dt']
        available_at = arr_dt if pd.notna(arr_dt) else query_dt

        candidates.append({
            "tail_number": tail,
            "available_at": available_at,
            "own_remaining_legs": day[owning_index + 1:],
        })

    return candidates


def _day_filtered_legs_by_tail(legs_by_tail, date):
    """Precompute {tail: day_legs} once, for callers that invoke
    find_swap_candidates repeatedly with the same `date` -- avoids re-scanning
    each tail's full history on every call.
    """
    result = {}
    for tail, legs in legs_by_tail.items():
        day = [leg for leg in legs if leg[2]['flight_date'] == date]
        if day:
            result[tail] = day
    return result


# Return-trip repositioning estimate constants -- stated modeling values,
# not derived/fitted from data.
CRUISE_SPEED_KTS = 450.0  # rough commercial jet cruise speed
TURNAROUND_BUFFER_MINUTES = 45.0  # ground/turnaround buffer on top of a ferry flight

# Fallback when an airport is missing lat/lon in the source graph. Rare.
MISSING_COORDS_FALLBACK_MINUTES = 180.0


def _build_departure_index(legs_by_tail):
    """{(airport, date): sorted list of crs_dep_dt}, built once. Lets
    connecting_flights_missed use a bisect lookup instead of scanning every
    tail's every leg on each call.
    """
    by_airport_date = {}
    for legs in legs_by_tail.values():
        for u, v, d in legs:
            key = (u, d['flight_date'])
            by_airport_date.setdefault(key, []).append(d['crs_dep_dt'])
    return {key: sorted(times) for key, times in by_airport_date.items()}


def _build_route_duration_table(legs_by_tail):
    """Average real scheduled flight duration (minutes) per (origin,
    destination) route, built once. Kept separate from route_table
    (analysis/queries.py) since that schema has no duration column and
    other callers depend on it unchanged.
    """
    totals, counts = {}, {}
    for legs in legs_by_tail.values():
        for u, v, d in legs:
            crs_dep, crs_arr = d.get('crs_dep_dt'), d.get('crs_arr_dt')
            if pd.isna(crs_dep) or pd.isna(crs_arr):
                continue
            minutes = (crs_arr - crs_dep).total_seconds() / 60
            key = (u, v)
            totals[key] = totals.get(key, 0.0) + minutes
            counts[key] = counts.get(key, 0) + 1
    return {key: totals[key] / counts[key] for key in totals}


def return_trip_estimate(from_airport, to_airport, route_duration_table, airport_coords):
    """Estimated minutes for a substitute aircraft to reposition back to
    to_airport. Feeds opportunity_cost as a single number, not a tracked leg
    with its own propagation (deliberately not simulating recursive cascades).

    Tier 1: real scheduled route -> its average duration (route_duration_table).
    Tier 2: no real route -> great-circle distance (analysis/geo.py) at
    CRUISE_SPEED_KTS plus TURNAROUND_BUFFER_MINUTES.
    """
    if from_airport == to_airport:
        return 0.0

    if (from_airport, to_airport) in route_duration_table:
        return route_duration_table[(from_airport, to_airport)]

    from_coords = airport_coords.get(from_airport)
    to_coords = airport_coords.get(to_airport)
    if from_coords is None or to_coords is None:
        return MISSING_COORDS_FALLBACK_MINUTES

    distance_nm = great_circle_distance_nm(from_coords[0], from_coords[1], to_coords[0], to_coords[1])
    flight_minutes = (distance_nm / CRUISE_SPEED_KTS) * 60
    return flight_minutes + TURNAROUND_BUFFER_MINUTES


def simulate_recovery(downstream_legs, substitute_available_at):
    """Propagate induced delay forward through a chain of downstream legs.

    Generic: callers decide whose chain and whose availability time to feed
    in -- an orphaned leg's downstream chain for induced_delay_minutes, or a
    candidate's own_remaining_legs for opportunity_cost. Runs off scheduled
    (crs_dep_dt/crs_arr_dt) times -- a hypothetical, not a reconciliation
    against historical actual delay.
    """
    if not downstream_legs:
        return 0.0, []

    if substitute_available_at is None or pd.isna(substitute_available_at):
        raise ValueError("substitute_available_at must be a valid timestamp")

    per_leg_breakdown = []
    total_induced_delay = 0.0
    prev_induced_delay = None
    prev_turnaround = None

    for u, v, d in downstream_legs:
        crs_dep = d['crs_dep_dt']
        crs_arr = d['crs_arr_dt']

        if prev_induced_delay is None:
            induced_delay = max(0.0, (substitute_available_at - crs_dep).total_seconds() / 60)
        else:
            buffer = prev_turnaround
            if buffer is None or pd.isna(buffer):
                buffer = 0.0  # missing buffer -- full delay carries forward
            induced_delay = max(0.0, prev_induced_delay - buffer)

        actual_departure = crs_dep + pd.Timedelta(minutes=induced_delay)
        actual_arrival = crs_arr + pd.Timedelta(minutes=induced_delay)

        per_leg_breakdown.append({
            "origin": u,
            "destination": v,
            "induced_delay_minutes": induced_delay,
            "actual_departure": actual_departure,
            "actual_arrival": actual_arrival,
        })

        total_induced_delay += induced_delay
        prev_induced_delay = induced_delay
        prev_turnaround = d['turnaround_to_next']

    return total_induced_delay, per_leg_breakdown


def compute_candidate_components(cancelled_leg, downstream_legs, candidate, route_table, centrality_table, max_flight_count, max_betweenness, route_duration_table, airport_coords):
    """The four raw cost/benefit components for one swap candidate, computed
    once regardless of ranking mode -- replacement_score only reweights them.

    opportunity_cost is return-trip-aware: it charges the candidate for the
    time spent flying the substitute chain plus repositioning back to its own
    next leg, rather than assuming it can resume its own schedule instantly
    from available_at (a bug found and fixed during Feature 2 validation).
    Mirrors compute_segment_cost's seeding logic.
    """
    induced_delay_minutes, downstream_breakdown = simulate_recovery(downstream_legs, candidate['available_at'])

    own_legs = candidate['own_remaining_legs']
    if not own_legs:
        opportunity_cost = 0.0
    elif not downstream_legs:
        # Nothing for the candidate to fly -- no repositioning cost.
        opportunity_cost, _ = simulate_recovery(own_legs, candidate['available_at'])
    else:
        segment_destination = downstream_legs[-1][1]
        last_actual_arrival = downstream_breakdown[-1]['actual_arrival']
        return_trip_minutes = return_trip_estimate(
            segment_destination, own_legs[0][0], route_duration_table, airport_coords
        )
        available_after_return = last_actual_arrival + pd.Timedelta(minutes=return_trip_minutes)
        opportunity_cost, _ = simulate_recovery(own_legs, available_after_return)

    cancelled_origin, cancelled_destination = cancelled_leg[0], cancelled_leg[1]
    importance_of_cancelled_leg = importance_weight(
        cancelled_origin, cancelled_destination, route_table, centrality_table, max_flight_count, max_betweenness
    )

    own_legs = candidate['own_remaining_legs']
    importance_of_candidates_own_legs = sum(
        importance_weight(u, v, route_table, centrality_table, max_flight_count, max_betweenness)
        for u, v, d in own_legs
    ) if own_legs else 0.0

    return {
        "induced_delay_minutes": induced_delay_minutes,
        "opportunity_cost": opportunity_cost,
        "importance_of_cancelled_leg": importance_of_cancelled_leg,
        "importance_of_candidates_own_legs": importance_of_candidates_own_legs,
        "delay": induced_delay_minutes,
        "network_disruption": induced_delay_minutes + opportunity_cost,
    }


def compute_segment_cost(candidate, segment_legs, route_table, centrality_table, max_flight_count, max_betweenness, route_duration_table, airport_coords):
    """Feature 2's per-(candidate, contiguous segment) analog of
    compute_candidate_components, generalized from a single cancelled_leg to
    a run of orphaned segment_legs.

    opportunity_cost is seeded by a return-trip-adjusted availability time
    (return_trip_estimate), since a segment can end away from where the
    candidate's own remaining legs begin.

    Returns the same four keys as compute_candidate_components (so
    replacement_score works unchanged); importance_of_cancelled_leg here
    means the summed importance of every leg in the segment.
    """
    induced_delay_minutes, breakdown = simulate_recovery(segment_legs, candidate['available_at'])

    own_legs = candidate['own_remaining_legs']
    segment_destination = segment_legs[-1][1]
    last_actual_arrival = breakdown[-1]['actual_arrival']

    if own_legs:
        return_trip_minutes = return_trip_estimate(
            segment_destination, own_legs[0][0], route_duration_table, airport_coords
        )
        available_after_return = last_actual_arrival + pd.Timedelta(minutes=return_trip_minutes)
        opportunity_cost, _ = simulate_recovery(own_legs, available_after_return)
    else:
        return_trip_minutes = 0.0
        opportunity_cost = 0.0

    importance_of_segment_legs = sum(
        importance_weight(u, v, route_table, centrality_table, max_flight_count, max_betweenness)
        for u, v, d in segment_legs
    )
    importance_of_candidates_own_legs = sum(
        importance_weight(u, v, route_table, centrality_table, max_flight_count, max_betweenness)
        for u, v, d in own_legs
    ) if own_legs else 0.0

    return {
        "induced_delay_minutes": induced_delay_minutes,
        "opportunity_cost": opportunity_cost,
        "importance_of_cancelled_leg": importance_of_segment_legs,
        "importance_of_candidates_own_legs": importance_of_candidates_own_legs,
        "return_trip_minutes": return_trip_minutes,
    }


def connecting_flights_missed(cancelled_leg, candidate, downstream_legs, legs_by_tail, departure_index=None):
    """Proxy for connecting passengers stranded by this candidate's delay,
    NOT a real passenger count. Counts scheduled departures from the first
    downstream leg's destination airport that fall between its original and
    simulated (delayed) arrival -- flights missed because arrival slipped
    past their departure.

    departure_index: optional {(airport, date): sorted crs_dep_dt list} from
    _build_departure_index for a bisect lookup instead of a linear scan.
    Defaults to None (original scan behavior).
    """
    if not downstream_legs:
        return 0

    _, breakdown = simulate_recovery(downstream_legs, candidate['available_at'])
    first_leg_destination = downstream_legs[0][1]
    first_leg_data = downstream_legs[0][2]
    window_start = first_leg_data['crs_arr_dt']
    window_end = breakdown[0]['actual_arrival']

    if window_end <= window_start:
        return 0  # arrived on time or early -- no missed connections

    date = first_leg_data['flight_date']

    if departure_index is not None:
        times = departure_index.get((first_leg_destination, date), [])
        return bisect_left(times, window_end) - bisect_right(times, window_start)

    count = 0
    for legs in legs_by_tail.values():
        for u, v, d in legs:
            if u == first_leg_destination and d['flight_date'] == date and window_start < d['crs_dep_dt'] < window_end:
                count += 1
    return count


# Five weight vectors over the same three factors -- stated modeling
# choices for stress-testing recommendation sensitivity, not fitted values.
RANKING_MODES = {
    "minimize_this_flight_delay": {"delay": 0.7, "opportunity_cost": 0.15, "importance": 0.15},
    "minimize_total_delay": {"delay": 0.45, "opportunity_cost": 0.45, "importance": 0.10},
    "protect_other_flights": {"delay": 0.15, "opportunity_cost": 0.70, "importance": 0.15},
    "protect_major_flights": {"delay": 0.15, "opportunity_cost": 0.15, "importance": 0.70},
    "all_factors_combined": {"delay": 0.34, "opportunity_cost": 0.33, "importance": 0.33},
}


def replacement_score(components, mode):
    """Single sort key (lower = better) for one candidate under one ranking
    mode. Same formula for every mode, only the weight vector changes -- so a
    candidate strictly worse on every raw component always scores worse
    (never recommends a Pareto-dominated candidate).
    """
    weights = RANKING_MODES[mode]
    return (
        weights["delay"] * components["induced_delay_minutes"]
        + weights["opportunity_cost"] * components["opportunity_cost"]
        + weights["importance"] * (
            components["importance_of_cancelled_leg"] + components["importance_of_candidates_own_legs"]
        )
    )


def rank_candidates(cancelled_leg, downstream_legs, candidates, mode, route_table, centrality_table, legs_by_tail, route_duration_table=None, airport_coords=None, departure_index=None):
    """Score and sort every candidate under one ranking mode. Components and
    the connecting_flights_missed proxy are computed once per candidate --
    mode only changes how they're weighted into the final sort key.

    route_duration_table/airport_coords feed compute_candidate_components's
    return-trip-aware opportunity_cost; built/defaulted here if not supplied.
    Without real airport_coords, Tier 2 return-trip legs fall back to
    return_trip_estimate's flat MISSING_COORDS_FALLBACK_MINUTES.

    departure_index: optional, passed through to connecting_flights_missed.
    """
    max_flight_count = route_table['flight_count'].max()
    max_betweenness = max(centrality_table.values())
    if route_duration_table is None:
        route_duration_table = _build_route_duration_table(legs_by_tail)
    if airport_coords is None:
        airport_coords = {}

    ranked = []
    for candidate in candidates:
        components = compute_candidate_components(
            cancelled_leg, downstream_legs, candidate, route_table, centrality_table, max_flight_count, max_betweenness,
            route_duration_table, airport_coords,
        )
        score = replacement_score(components, mode)
        missed = connecting_flights_missed(cancelled_leg, candidate, downstream_legs, legs_by_tail, departure_index)

        ranked.append({
            "tail_number": candidate["tail_number"],
            "score": score,
            "delay": components["delay"],
            "network_disruption": components["network_disruption"],
            "connecting_flights_missed": missed,
        })

    ranked.sort(key=lambda c: c["score"])
    return ranked


def get_orphaned_legs(legs_by_tail, tail_number, date, query_time):
    """Feature 2's analog of get_downstream_legs: every leg still scheduled
    on tail_number's date strictly after query_time -- the whole remaining
    chain orphaned by the tail going down entirely, not just one leg's
    downstream. Uses crs_dep_dt (scheduled), matching this module's
    scheduled-time convention.
    """
    query_dt = pd.to_datetime(f"{date} {query_time}")
    day_legs = [leg for leg in legs_by_tail.get(tail_number, []) if leg[2]['flight_date'] == date]
    return [leg for leg in day_legs if leg[2]['crs_dep_dt'] > query_dt]


def _best_segment_for_candidate(candidate, orphaned_legs, start_idx, route_table, centrality_table, max_flight_count, max_betweenness, route_duration_table, airport_coords, mode):
    """Greedy extend-or-stop for ONE candidate starting at
    orphaned_legs[start_idx]: extend the segment one leg at a time only while
    it lowers replacement_score; stop at the first extension that doesn't.
    """
    length = 1
    components = compute_segment_cost(
        candidate, orphaned_legs[start_idx:start_idx + length],
        route_table, centrality_table, max_flight_count, max_betweenness,
        route_duration_table, airport_coords,
    )
    best_score = replacement_score(components, mode)
    best_length, best_components = length, components

    while start_idx + length < len(orphaned_legs):
        trial_length = length + 1
        trial_components = compute_segment_cost(
            candidate, orphaned_legs[start_idx:start_idx + trial_length],
            route_table, centrality_table, max_flight_count, max_betweenness,
            route_duration_table, airport_coords,
        )
        trial_score = replacement_score(trial_components, mode)
        if trial_score >= best_score:
            break
        best_score, best_length, best_components = trial_score, trial_length, trial_components
        length = trial_length

    return best_length, best_components, best_score


def assign_recovery_segments(tail, date, query_time, legs_by_tail, route_table, centrality_table, airport_coords, mode="all_factors_combined", route_duration_table=None):
    """Feature 2 greedy solver (whole-aircraft-down): the analog of
    compute_recovery_improvement for when tail's entire remaining day, not
    just one leg, is orphaned. Requires airport_coords (return_trip_estimate's
    ferry fallback needs real lat/lon -- build it from the rotation graph's
    node data).

    Every leg after query_time on date is orphaned at once (get_orphaned_legs).
    Unlike Feature 1, one substitute may not cover the whole chain: a
    candidate covers a contiguous run of legs, extended one at a time only
    while replacement_score says extending is cheaper than stopping
    (_best_segment_for_candidate). The candidate with the best score for its
    own best segment length wins that starting leg.

    A starting leg with no candidate at all is recorded uncovered and the
    search continues -- an expected outcome (supply shortfall is the
    dominant real-world constraint under simultaneous cancellations, see
    analysis/experiments/mass_disruption_stress_test.py), not an error.

    This is the greedy baseline; assign_recovery_segments_optimal is the
    exhaustive-search version.

    Returns:
        {
            "segments": [
                {
                    "tail_number": str or None (None = uncovered),
                    "legs_covered": [(origin, destination), ...],
                    "induced_delay_minutes": float or None,
                    "opportunity_cost": float or None,
                    "return_trip_minutes": float or None,
                    "score": float or None,
                },
                ...
            ],
            "total_orphaned_legs": int,
            "total_covered_legs": int,
            "total_uncovered_legs": int,
            "num_segments": int,  # covered segments only
            "total_induced_delay_minutes": float,  # covered segments only
        }
    """
    if route_duration_table is None:
        route_duration_table = _build_route_duration_table(legs_by_tail)

    max_flight_count = route_table['flight_count'].max()
    max_betweenness = max(centrality_table.values())

    orphaned_legs = get_orphaned_legs(legs_by_tail, tail, date, query_time)

    # Precompute the day-filtered view once (see _day_filtered_legs_by_tail)
    # instead of re-scanning every tail's history at every position.
    day_legs_by_tail = _day_filtered_legs_by_tail(legs_by_tail, date)

    # Tails already assigned to an earlier segment in this batch -- without
    # this, the same tail could be recommended to cover two segments at once.
    claimed = set()

    segments = []
    start_idx = 0
    while start_idx < len(orphaned_legs):
        leg = orphaned_legs[start_idx]
        origin = leg[0]
        leg_query_time = leg[2]['crs_dep_dt'].strftime('%H:%M')

        candidates = find_swap_candidates(day_legs_by_tail, origin, date, leg_query_time)
        # Exclude the down tail itself (BTS data doesn't know about this
        # hypothetical event, so it could otherwise appear "parked" between
        # its own orphaned legs) and tails already claimed this batch.
        candidates = [c for c in candidates if c['tail_number'] != tail and c['tail_number'] not in claimed]

        if not candidates:
            segments.append({
                "tail_number": None,
                "legs_covered": [(leg[0], leg[1])],
                "induced_delay_minutes": None,
                "opportunity_cost": None,
                "return_trip_minutes": None,
                "score": None,
            })
            start_idx += 1
            continue

        best = None
        for candidate in candidates:
            length, components, score = _best_segment_for_candidate(
                candidate, orphaned_legs, start_idx,
                route_table, centrality_table, max_flight_count, max_betweenness,
                route_duration_table, airport_coords, mode,
            )
            if best is None or score < best["score"]:
                best = {
                    "tail_number": candidate["tail_number"],
                    "length": length,
                    "components": components,
                    "score": score,
                }

        covered_legs = orphaned_legs[start_idx:start_idx + best["length"]]
        segments.append({
            "tail_number": best["tail_number"],
            "legs_covered": [(u, v) for u, v, d in covered_legs],
            "induced_delay_minutes": best["components"]["induced_delay_minutes"],
            "opportunity_cost": best["components"]["opportunity_cost"],
            "return_trip_minutes": best["components"]["return_trip_minutes"],
            "score": best["score"],
        })
        claimed.add(best["tail_number"])
        start_idx += best["length"]

    covered_segments = [s for s in segments if s["tail_number"] is not None]
    uncovered_segments = [s for s in segments if s["tail_number"] is None]

    return {
        "segments": segments,
        "total_orphaned_legs": len(orphaned_legs),
        "total_covered_legs": sum(len(s["legs_covered"]) for s in covered_segments),
        "total_uncovered_legs": sum(len(s["legs_covered"]) for s in uncovered_segments),
        "num_segments": len(covered_segments),
        "total_induced_delay_minutes": sum(s["induced_delay_minutes"] for s in covered_segments),
    }


# Assumed modeling estimate for an outright cancellation's cost, in
# delay-minute-equivalent terms -- NOT derived from data. Only affects the
# headline percentage; cancellations_avoided/total_induced_delay_minutes
# never depend on it.
CANCELLATION_PENALTY_MINUTES = 300.0


# assign_recovery_segments_optimal's (position, used-tail-set) state space is
# unbounded by candidate-pool size, and memoization barely collapses it since
# `used` tracks specific tail identity -- unbounded DP runs produced
# multi-minute hangs. Two independent safety mechanisms:
MAX_CANDIDATES_PER_POSITION = 8   # branching-factor narrowing (see _top_candidates) -- a heuristic, not a correctness guarantee
MAX_DP_STATES = 10_000            # hard ceiling on states explored (see solve()); exceeding it aborts to the greedy fallback


def compute_recovery_improvement(cancelled_leg, downstream_legs, candidates, mode, route_table, centrality_table, legs_by_tail, route_duration_table=None, airport_coords=None, ranked=None, departure_index=None):
    """Compares the baseline (no intervention -- every downstream leg
    cancelled) against the best-ranked candidate under the given mode.
    Returns an honest two-number breakdown plus a headline percentage built
    from a disclosed penalty constant.

    route_duration_table/airport_coords/departure_index pass through to
    rank_candidates unchanged (ignored if `ranked` is already supplied).
    ranked: pass an already-computed rank_candidates(...) result to skip
    re-ranking when the caller needs it for its own purposes too.

    Returns:
        {
            "cancellations_avoided": int,
            "total_induced_delay_minutes": float or None,
            "baseline_cost_minutes": float,
            "improvement_pct": float or None,
            "best_candidate": str (tail_number) or None,
        }
    """
    cancellations_avoided = len(downstream_legs)
    baseline_cost_minutes = cancellations_avoided * CANCELLATION_PENALTY_MINUTES

    if not candidates:
        # No viable candidate -- outcome is the baseline; 0% improvement is
        # the honest characterization since nothing changed.
        return {
            "cancellations_avoided": cancellations_avoided,
            "total_induced_delay_minutes": None,
            "baseline_cost_minutes": baseline_cost_minutes,
            "improvement_pct": 0.0,
            "best_candidate": None,
        }

    if ranked is None:
        ranked = rank_candidates(
            cancelled_leg, downstream_legs, candidates, mode, route_table, centrality_table, legs_by_tail,
            route_duration_table, airport_coords, departure_index,
        )
    best = ranked[0]
    total_induced_delay_minutes = best["delay"]

    if baseline_cost_minutes == 0:
        # Nothing to disrupt in the first place -- "100% avoided" is the
        # vacuous-but-honest answer rather than an undefined 0/0 division.
        improvement_pct = 100.0
    else:
        improvement_pct = (baseline_cost_minutes - total_induced_delay_minutes) / baseline_cost_minutes * 100

    return {
        "cancellations_avoided": cancellations_avoided,
        "total_induced_delay_minutes": total_induced_delay_minutes,
        "baseline_cost_minutes": baseline_cost_minutes,
        "improvement_pct": improvement_pct,
        "best_candidate": best["tail_number"],
    }


def _uncovered_segment(leg):
    return {
        "tail_number": None,
        "legs_covered": [(leg[0], leg[1])],
        "induced_delay_minutes": None,
        "opportunity_cost": None,
        "return_trip_minutes": None,
        "score": None,
    }


class _DPStateBudgetExceeded(Exception):
    """Internal control-flow signal: raised by solve() once MAX_DP_STATES
    states have been explored, caught at the top of
    assign_recovery_segments_optimal. Used instead of threading a sentinel
    through solve/best_for_candidate's mutual recursion, so the abort is a
    single unwind rather than a check at every return point.
    """


def _top_candidates(pool, leg, route_table, centrality_table, max_flight_count, max_betweenness,
                     route_duration_table, airport_coords, mode, limit=MAX_CANDIDATES_PER_POSITION):
    """Rank `pool` by each candidate's length-1-segment replacement_score for
    this leg and keep the top `limit`. A cheap heuristic to shrink the DP's
    branching factor -- not the DP's real objective, which can extend
    segments past length 1 and sees `used`-set exclusions this can't.
    """
    if len(pool) <= limit:
        return pool
    scored = [
        (replacement_score(
            compute_segment_cost(c, [leg], route_table, centrality_table,
                                  max_flight_count, max_betweenness, route_duration_table, airport_coords),
            mode,
        ), c)
        for c in pool
    ]
    scored.sort(key=lambda pair: pair[0])
    return [c for _, c in scored[:limit]]


def assign_recovery_segments_optimal(tail, date, query_time, legs_by_tail,
                                       route_table, centrality_table, airport_coords,
                                       mode="all_factors_combined", route_duration_table=None):
    """Optimal version of assign_recovery_segments: considers the full
    segmentation+assignment space via memoized recursion over
    (position, used-candidates) state, instead of committing greedily.
    Reuses compute_segment_cost/replacement_score/CANCELLATION_PENALTY_MINUTES
    -- only the search strategy is new. Requires airport_coords, same reason
    as assign_recovery_segments.

    State: (position in orphaned_legs, frozenset of tails already used by an
    earlier segment in this path) -- the used-set prevents the same physical
    impossibility Part 1's `claimed` set fixes for the greedy solver. Candidate
    pools aren't bounded by the small orphaned-leg count, so two safety
    mechanisms keep this tractable: candidates_at is narrowed to the top
    MAX_CANDIDATES_PER_POSITION per position (_top_candidates), and solve()
    aborts via _DPStateBudgetExceeded past MAX_DP_STATES states, falling back
    to the greedy result (which trivially satisfies "optimal >= greedy").

    Edges out of a state (position, used):
      - "assign": pick an eligible candidate and segment length -- weight is
        compute_segment_cost's replacement_score for that pair.
      - "uncovered": skip this leg -- weight is CANCELLATION_PENALTY_MINUTES.
    Goal: shortest path from (0, frozenset()) to position == len(orphaned_legs).

    Returns the same shape as assign_recovery_segments.
    """
    if route_duration_table is None:
        route_duration_table = _build_route_duration_table(legs_by_tail)

    max_flight_count = route_table['flight_count'].max()
    max_betweenness = max(centrality_table.values())

    orphaned_legs = get_orphaned_legs(legs_by_tail, tail, date, query_time)
    n = len(orphaned_legs)

    # Precompute the day-filtered view once (see _day_filtered_legs_by_tail)
    # instead of re-scanning every tail's history at every position.
    day_legs_by_tail = _day_filtered_legs_by_tail(legs_by_tail, date)

    # Eligible candidates per position depend only on (origin, query_time),
    # never on `used` -- computed once per position, not once per DP state.
    candidates_at = []
    for leg in orphaned_legs:
        leg_query_time = leg[2]['crs_dep_dt'].strftime('%H:%M')
        pool = find_swap_candidates(day_legs_by_tail, leg[0], date, leg_query_time)
        pool = [c for c in pool if c['tail_number'] != tail]
        candidates_at.append(_top_candidates(
            pool, leg, route_table, centrality_table, max_flight_count, max_betweenness,
            route_duration_table, airport_coords, mode,
        ))

    memo = {}
    # (position, candidate_tail, length) -> (components, score). Unlike memo
    # (keyed on `used`), segment cost never depends on `used` -- without this
    # cache, the same segment cost would be recomputed for every distinct
    # `used` set reaching this position. Pure caching, doesn't change results
    # (confirmed via byte-identical before/after diffs on 5 real cases).
    segment_cost_cache = {}

    # Distinct (position, used) states explored (memo misses only) -- the
    # hard ceiling on exhaustive search; see MAX_DP_STATES and solve() below.
    state_count = 0

    def best_for_candidate(position, candidate, used):
        """Best (cost, segments-for-suffix) over every segment length this
        candidate could take starting at `position`. Split out from solve()
        to keep each function's branching shallow.
        """
        candidate_tail = candidate["tail_number"]
        best_cost, best_segments = None, None
        for length in range(1, n - position + 1):
            segment_legs = orphaned_legs[position:position + length]
            cache_key = (position, candidate_tail, length)
            if cache_key in segment_cost_cache:
                components, score = segment_cost_cache[cache_key]
            else:
                components = compute_segment_cost(
                    candidate, segment_legs, route_table, centrality_table,
                    max_flight_count, max_betweenness, route_duration_table, airport_coords,
                )
                score = replacement_score(components, mode)
                segment_cost_cache[cache_key] = (components, score)
            rest_cost, rest_segments = solve(position + length, used | frozenset({candidate_tail}))
            total_cost = score + rest_cost
            if best_cost is None or total_cost < best_cost:
                segment = {
                    "tail_number": candidate_tail,
                    "legs_covered": [(u, v) for u, v, d in segment_legs],
                    "induced_delay_minutes": components["induced_delay_minutes"],
                    "opportunity_cost": components["opportunity_cost"],
                    "return_trip_minutes": components["return_trip_minutes"],
                    "score": score,
                }
                best_cost, best_segments = total_cost, [segment] + rest_segments
        return best_cost, best_segments

    def solve(position, used):
        nonlocal state_count
        if position == n:
            return 0.0, []

        key = (position, used)
        if key in memo:
            return memo[key]

        state_count += 1
        if state_count > MAX_DP_STATES:
            raise _DPStateBudgetExceeded()

        uncovered_cost, uncovered_rest = solve(position + 1, used)
        best_cost = CANCELLATION_PENALTY_MINUTES + uncovered_cost
        best_segments = [_uncovered_segment(orphaned_legs[position])] + uncovered_rest

        for candidate in candidates_at[position]:
            if candidate["tail_number"] in used:
                continue
            cost, segments = best_for_candidate(position, candidate, used)
            if cost < best_cost:
                best_cost, best_segments = cost, segments

        memo[key] = (best_cost, best_segments)
        return memo[key]

    try:
        _, segments = solve(0, frozenset())
    except _DPStateBudgetExceeded:
        # Blew past MAX_DP_STATES -- defer to the greedy solver rather than
        # return a partial/unsound search. Same shape, trivially satisfies
        # "optimal never worse than greedy" (equality here).
        return assign_recovery_segments(
            tail, date, query_time, legs_by_tail, route_table, centrality_table,
            airport_coords, mode=mode, route_duration_table=route_duration_table,
        )

    covered_segments = [s for s in segments if s["tail_number"] is not None]
    uncovered_segments = [s for s in segments if s["tail_number"] is None]

    return {
        "segments": segments,
        "total_orphaned_legs": n,
        "total_covered_legs": sum(len(s["legs_covered"]) for s in covered_segments),
        "total_uncovered_legs": sum(len(s["legs_covered"]) for s in uncovered_segments),
        "num_segments": len(covered_segments),
        "total_induced_delay_minutes": sum(s["induced_delay_minutes"] for s in covered_segments),
    }


if __name__ == "__main__":
    import math
    import pickle
    from pathlib import Path

    GRAPH_PATH = Path(__file__).parent.parent / "data" / "delta_rotation_graph.pkl"
    with open(GRAPH_PATH, "rb") as f:
        G = pickle.load(f)

    from analysis.queries import compute_route_delay_summary
    from analysis.centrality import build_weighted_graph, compute_betweenness_centrality

    df = pd.read_csv(GRAPH_PATH.parent / "delta_ontime_with_datetimes.csv", low_memory=False)
    route_table = compute_route_delay_summary(df)

    G_weighted = build_weighted_graph(G)
    centrality_table = compute_betweenness_centrality(G_weighted)

    legs_by_tail = {}
    for u, v, d in G.edges(data=True):
        legs_by_tail.setdefault(d['tail_number'], []).append((u, v, d))
    for tail in legs_by_tail:
        legs_by_tail[tail].sort(key=lambda e: e[2]['crs_dep_dt'])

    # Built once, up front, so every test below uses real return-trip data
    # instead of the {}/auto-build defaults.
    airport_coords = {
        code: (data['lat'], data['lon'])
        for code, data in G.nodes(data=True)
        if data.get('lat') is not None and data.get('lon') is not None
    }
    route_duration_table = _build_route_duration_table(legs_by_tail)

    # N101DQ lands JFK->MCO (actual arrival 09:53) on 2025-01-01, then sits at
    # MCO until its own MCO->SLC leg (scheduled 11:10) -- a known candidate at 10:30.
    candidates = find_swap_candidates(legs_by_tail, 'MCO', '2025-01-01', '10:30')
    by_tail = {c['tail_number']: c for c in candidates}
    assert 'N101DQ' in by_tail, "N101DQ should be a known candidate parked at MCO at 10:30"

    entry = by_tail['N101DQ']
    assert entry['available_at'] == pd.Timestamp('2025-01-01 09:53:00'), (
        f"available_at should be the tail's actual arrival time, got {entry['available_at']}"
    )
    assert [(u, v) for u, v, _ in entry['own_remaining_legs']] == [('MCO', 'SLC'), ('SLC', 'MSP'), ('MSP', 'BZN')], (
        f"own_remaining_legs should carry this tail's own downstream legs for the day, "
        f"got {[(u, v) for u, v, _ in entry['own_remaining_legs']]}"
    )
    print(f"MCO @ 10:30: {len(candidates)} candidates, N101DQ available_at={entry['available_at']}, "
          f"own_remaining_legs={[(u, v) for u, v, _ in entry['own_remaining_legs']]}")

    # Confirms the old max_candidates=5 cutoff no longer truncates the pool.
    atl_candidates = find_swap_candidates(legs_by_tail, 'ATL', '2025-01-01', '10:30')
    assert len(atl_candidates) > 5, (
        f"expected more than 5 candidates at ATL to prove the early cutoff is gone, "
        f"got {len(atl_candidates)}"
    )
    print(f"ATL @ 10:30: {len(atl_candidates)} candidates (old code would have capped this at 5)")

    # --- find_swap_candidates: "flew today" filter, real leaky-parked case ---
    # N178DN / 2025-09-10: JFK->SFO, Diverted, dep_dt/wheels_off real but
    # arr_dt/wheels_on are NaT. Only leg that tail has that day.
    n178dn_day = [leg for leg in legs_by_tail['N178DN'] if leg[2]['flight_date'] == '2025-09-10']
    assert len(n178dn_day) == 1, f"expected exactly one leg for N178DN on 2025-09-10, got {len(n178dn_day)}"
    leg_data = n178dn_day[0][2]
    assert leg_data['cancelled'] == 0.0 and pd.notna(leg_data['dep_dt']) and pd.isna(leg_data['arr_dt']), (
        "expected N178DN's leg to be the not-cancelled/real-departure/NaT-arrival case this filter targets"
    )

    # Confirms classify_aircraft_status's leak is real: this leg still reads
    # as 'parked' at SFO, proving find_swap_candidates's filter does real work.
    leaky_state = get_aircraft_state(n178dn_day, pd.to_datetime('2025-09-10 20:00'))
    assert leaky_state['status'] == 'parked' and leaky_state['destination'] == 'SFO', (
        f"expected the pre-existing NaT-arrival leak to still classify this leg as parked at SFO, "
        f"got {leaky_state}"
    )

    sfo_candidates = find_swap_candidates(legs_by_tail, 'SFO', '2025-09-10', '20:00')
    sfo_tails = {c['tail_number'] for c in sfo_candidates}
    assert 'N178DN' not in sfo_tails, (
        "N178DN has zero completed real legs on 2025-09-10 (departed but never got a real "
        "arrival) -- the 'flew today' filter should exclude it even though classify_aircraft_status "
        "reads it as parked"
    )
    print(f"SFO @ 2025-09-10 20:00: N178DN correctly excluded from {len(sfo_candidates)} candidates "
          f"despite classify_aircraft_status's leaky 'parked' read ({leaky_state}).")

    # --- simulate_recovery: synthetic chain ---
    # A->B has a 60 min buffer to B->C; B->C has a 45 min buffer to C->D;
    # C->D is the last leg (turnaround_to_next=None).
    leg_ab = ('A', 'B', {
        'crs_dep_dt': pd.Timestamp('2025-01-01 10:00'),
        'crs_arr_dt': pd.Timestamp('2025-01-01 11:00'),
        'turnaround_to_next': 60.0,
    })
    leg_bc = ('B', 'C', {
        'crs_dep_dt': pd.Timestamp('2025-01-01 12:00'),
        'crs_arr_dt': pd.Timestamp('2025-01-01 13:00'),
        'turnaround_to_next': 45.0,
    })
    leg_cd = ('C', 'D', {
        'crs_dep_dt': pd.Timestamp('2025-01-01 13:45'),
        'crs_arr_dt': pd.Timestamp('2025-01-01 14:45'),
        'turnaround_to_next': None,
    })
    synthetic_chain = [leg_ab, leg_bc, leg_cd]

    # Case 1: substitute exactly on time -> zero delay everywhere.
    total, breakdown = simulate_recovery(synthetic_chain, pd.Timestamp('2025-01-01 10:00'))
    assert total == 0.0, f"expected 0.0 total delay when on time, got {total}"
    assert all(leg['induced_delay_minutes'] == 0.0 for leg in breakdown), (
        f"expected every leg to show 0.0 induced delay when on time, got {breakdown}"
    )
    print(f"Case 1 (on time): total={total}")

    # Case 2: 30 min late -- A->B shows the delay, its 60 min buffer fully
    # absorbs it before reaching B->C.
    total, breakdown = simulate_recovery(synthetic_chain, pd.Timestamp('2025-01-01 10:30'))
    assert breakdown[0]['induced_delay_minutes'] == 30.0, (
        f"expected leg A->B (the late leg) to show 30.0 induced delay, got {breakdown[0]}"
    )
    assert breakdown[1]['induced_delay_minutes'] == 0.0, (
        f"expected leg B->C to show 0.0 induced delay (A->B's 60 min buffer absorbs the 30 min delay), got {breakdown[1]}"
    )
    assert breakdown[2]['induced_delay_minutes'] == 0.0, "expected nothing to carry to C->D either"
    print(f"Case 2 (30 min late, buffer absorbs): total={total}, per-leg={[b['induced_delay_minutes'] for b in breakdown]}")

    # Case 3: 90 min late -- exceeds A->B's 60 min buffer, 30 min excess
    # carries to B->C, whose 45 min buffer then absorbs it.
    total, breakdown = simulate_recovery(synthetic_chain, pd.Timestamp('2025-01-01 11:30'))
    assert breakdown[0]['induced_delay_minutes'] == 90.0, f"expected A->B to show 90.0, got {breakdown[0]}"
    assert breakdown[1]['induced_delay_minutes'] == 30.0, (
        f"expected B->C to show 30.0 (90 - 60 buffer excess carried forward), got {breakdown[1]}"
    )
    assert breakdown[2]['induced_delay_minutes'] == 0.0, (
        f"expected C->D to show 0.0 (B->C's 45 min buffer absorbs the 30 min carry-forward), got {breakdown[2]}"
    )
    assert total == 120.0, f"expected total 120.0 (90 + 30 + 0), got {total}"
    print(f"Case 3 (90 min late, excess carries): total={total}, per-leg={[b['induced_delay_minutes'] for b in breakdown]}")

    # Case 4: empty downstream_legs -> (0.0, []), not an error.
    assert simulate_recovery([], pd.Timestamp('2025-01-01 10:00')) == (0.0, []), (
        "expected (0.0, []) for an empty downstream_legs list"
    )
    print("Case 4 (empty downstream_legs): (0.0, []) as expected")

    # Case 5: missing turnaround_to_next -- treated as zero buffer, full
    # delay carries forward.
    leg_missing_buffer = ('X', 'Y', {
        'crs_dep_dt': pd.Timestamp('2025-01-01 08:00'),
        'crs_arr_dt': pd.Timestamp('2025-01-01 09:00'),
        'turnaround_to_next': None,
    })
    leg_after_missing_buffer = ('Y', 'Z', {
        'crs_dep_dt': pd.Timestamp('2025-01-01 09:30'),
        'crs_arr_dt': pd.Timestamp('2025-01-01 10:30'),
        'turnaround_to_next': 30.0,
    })
    total, breakdown = simulate_recovery(
        [leg_missing_buffer, leg_after_missing_buffer], pd.Timestamp('2025-01-01 08:20')
    )
    assert breakdown[0]['induced_delay_minutes'] == 20.0, f"expected X->Y to show 20.0, got {breakdown[0]}"
    assert breakdown[1]['induced_delay_minutes'] == 20.0, (
        f"expected Y->Z to carry the full 20.0 (missing buffer treated as zero), got {breakdown[1]}"
    )
    print(f"Case 5 (missing turnaround_to_next): per-leg={[b['induced_delay_minutes'] for b in breakdown]}")

    # Case 6: invalid substitute_available_at raises rather than propagating NaN.
    try:
        simulate_recovery(synthetic_chain, None)
        assert False, "expected ValueError for substitute_available_at=None"
    except ValueError:
        pass
    print("Case 6 (substitute_available_at=None): raised ValueError as expected")

    # --- simulate_recovery: real N101DQ / 2025-01-01 scenario ---
    # Substitute available 13:00 (110 min late vs. MCO->SLC's 11:10 dep).
    # Hand trace: MCO->SLC=110, SLC->MSP=max(0,110-71)=39, MSP->BZN=max(0,39-168)=0. Total=149.0.
    cancelled_leg_real, real_downstream = get_downstream_legs(legs_by_tail, 'N101DQ', '2025-01-01', 'JFK', 'MCO')
    total, breakdown = simulate_recovery(real_downstream, pd.Timestamp('2025-01-01 13:00:00'))
    expected_per_leg = [110.0, 39.0, 0.0]
    actual_per_leg = [leg['induced_delay_minutes'] for leg in breakdown]
    assert actual_per_leg == expected_per_leg, (
        f"hand trace expected {expected_per_leg}, got {actual_per_leg}"
    )
    assert total == 149.0, f"hand trace expected total 149.0, got {total}"
    print(f"Real N101DQ trace: hand-traced={expected_per_leg} (total 149.0), "
          f"function={actual_per_leg} (total {total})")
    for leg in breakdown:
        print(f"  {leg['origin']}->{leg['destination']}: "
              f"induced_delay={leg['induced_delay_minutes']} min, "
              f"actual_departure={leg['actual_departure']}, actual_arrival={leg['actual_arrival']}")

    # --- importance_weight: real high-centrality (ATL) vs high-traffic/low-centrality (Florida) route ---
    max_flight_count = route_table['flight_count'].max()
    max_betweenness = max(centrality_table.values())

    # MCO->RDU has MORE raw traffic than ATL->TUS, yet scores far lower --
    # proves centrality weighting dominates rather than just correlates.
    atl_traffic, atl_btw = route_table.loc[('ATL', 'TUS'), 'flight_count'], max(
        centrality_table.get('ATL', 0.0), centrality_table.get('TUS', 0.0)
    )
    mco_traffic, mco_btw = route_table.loc[('MCO', 'RDU'), 'flight_count'], max(
        centrality_table.get('MCO', 0.0), centrality_table.get('RDU', 0.0)
    )
    atl_score = importance_weight('ATL', 'TUS', route_table, centrality_table, max_flight_count, max_betweenness)
    mco_score = importance_weight('MCO', 'RDU', route_table, centrality_table, max_flight_count, max_betweenness)

    print(f"ATL->TUS: flight_count={atl_traffic}, max_betweenness={atl_btw}, importance_weight={atl_score}")
    print(f"MCO->RDU: flight_count={mco_traffic}, max_betweenness={mco_btw}, importance_weight={mco_score}")

    assert mco_traffic > atl_traffic, (
        "expected the Florida route to have MORE raw traffic than the ATL route "
        "-- this is what makes the centrality-weighting effect convincing"
    )
    assert atl_score > mco_score * 5, (
        f"expected ATL->TUS ({atl_score}) to score far higher than MCO->RDU ({mco_score}) "
        f"despite lower traffic, proving centrality weighting dominates"
    )
    print("importance_weight: centrality weighting confirmed to dominate over raw traffic.")

    # --- importance_weight: synthetic missing-data fallbacks (no real gaps exist in this dataset) ---
    missing_route_score = importance_weight('ATL', 'ZZZ', route_table, centrality_table, max_flight_count, max_betweenness)
    assert missing_route_score >= 0, "missing route should fall back to flight_count=0, not crash"
    print(f"Synthetic missing route (ATL->ZZZ, not in route_table): importance_weight={missing_route_score}")

    centrality_table_missing_atl = dict(centrality_table)
    del centrality_table_missing_atl['ATL']
    missing_airport_score = importance_weight(
        'ATL', 'TUS', route_table, centrality_table_missing_atl, max_flight_count, max_betweenness
    )
    assert missing_airport_score >= 0, "missing airport in centrality data should fall back to betweenness=0, not crash"
    print(f"Synthetic missing airport (ATL deleted from centrality_table): importance_weight={missing_airport_score}")

    # --- replacement_score: monotonicity ---
    # Hold everything fixed, worsen one component; no mode weighting that
    # factor > 0 should ever score the worse candidate better.
    base = {
        "induced_delay_minutes": 20.0,
        "opportunity_cost": 20.0,
        "importance_of_cancelled_leg": 20.0,
        "importance_of_candidates_own_legs": 20.0,
    }
    worse_delay = {**base, "induced_delay_minutes": 40.0}
    worse_opportunity_cost = {**base, "opportunity_cost": 40.0}
    worse_importance = {**base, "importance_of_cancelled_leg": 40.0}

    for mode, weights in RANKING_MODES.items():
        if weights["delay"] > 0:
            assert replacement_score(base, mode) < replacement_score(worse_delay, mode), (
                f"{mode}: worse induced_delay_minutes should never score better"
            )
        if weights["opportunity_cost"] > 0:
            assert replacement_score(base, mode) < replacement_score(worse_opportunity_cost, mode), (
                f"{mode}: worse opportunity_cost should never score better"
            )
        if weights["importance"] > 0:
            assert replacement_score(base, mode) < replacement_score(worse_importance, mode), (
                f"{mode}: worse importance_of_cancelled_leg should never score better"
            )
    print("replacement_score: monotonicity confirmed across all 5 modes.")

    # --- replacement_score: Pareto-dominance ---
    # "dominated" is strictly worse than "good" on all four components.
    # Tested at the component-dict level rather than via rank_candidates,
    # since importance_of_cancelled_leg is shared across candidates in one
    # real ranking call -- a genuine worse-on-all-four pair can't occur there.
    good = {
        "induced_delay_minutes": 10.0, "opportunity_cost": 10.0,
        "importance_of_cancelled_leg": 10.0, "importance_of_candidates_own_legs": 10.0,
    }
    dominated = {
        "induced_delay_minutes": 20.0, "opportunity_cost": 20.0,
        "importance_of_cancelled_leg": 20.0, "importance_of_candidates_own_legs": 20.0,
    }
    for mode in RANKING_MODES:
        good_score, dominated_score = replacement_score(good, mode), replacement_score(dominated, mode)
        assert good_score < dominated_score, (
            f"{mode}: Pareto-dominated candidate scored better ({dominated_score} vs {good_score}) -- must never happen"
        )
    print("replacement_score: Pareto-dominated candidate never wins, in any of the 5 modes.")

    # --- replacement_score: naive-baseline comparison + weight-sensitivity spot-check ---
    # X: low delay, high opportunity cost. Y: moderate delay, low opportunity
    # cost. Sorting by delay alone always picks X, ignoring the network cost.
    candidate_x = {
        "induced_delay_minutes": 5.0, "opportunity_cost": 60.0,
        "importance_of_cancelled_leg": 25.0, "importance_of_candidates_own_legs": 25.0,
    }
    candidate_y = {
        "induced_delay_minutes": 40.0, "opportunity_cost": 20.0,
        "importance_of_cancelled_leg": 25.0, "importance_of_candidates_own_legs": 25.0,
    }
    naive_pool = [("X", candidate_x), ("Y", candidate_y)]
    naive_winner, _ = min(naive_pool, key=lambda pair: pair[1]["induced_delay_minutes"])
    print(f"Naive baseline (sort by induced_delay_minutes alone) picks: {naive_winner}")

    print("Weight-sensitivity spot-check across all 5 modes:")
    for mode in RANKING_MODES:
        score_x, score_y = replacement_score(candidate_x, mode), replacement_score(candidate_y, mode)
        winner = "X" if score_x < score_y else "Y"
        agreement = "agrees with naive" if winner == naive_winner else "DISAGREES with naive"
        print(f"  {mode}: winner={winner} (X={score_x}, Y={score_y}) -- {agreement}")

    assert naive_winner == "X", "expected naive baseline to pick X (lower raw delay)"
    x_score, y_score = replacement_score(candidate_x, "protect_other_flights"), replacement_score(candidate_y, "protect_other_flights")
    assert y_score < x_score, (
        "expected protect_other_flights to flip the naive pick -- X's high opportunity_cost "
        "should make Y the better choice once opportunity cost is weighted heavily"
    )

    # --- replacement_score: extreme case, fully idle vs. busy candidate ---
    # Shared downstream_legs/available_at/cancelled_leg isolates the
    # own_remaining_legs difference (fake airports tie importance at 0).
    extreme_cancelled_leg = ('A', 'B', {})
    extreme_available_at = pd.Timestamp('2025-01-01 13:00:00')

    idle_candidate = {"tail_number": "IDLE1", "available_at": extreme_available_at, "own_remaining_legs": []}
    busy_candidate = {"tail_number": "BUSY1", "available_at": extreme_available_at, "own_remaining_legs": real_downstream}

    idle_components = compute_candidate_components(
        extreme_cancelled_leg, synthetic_chain, idle_candidate, route_table, centrality_table, max_flight_count, max_betweenness,
        route_duration_table, airport_coords,
    )
    busy_components = compute_candidate_components(
        extreme_cancelled_leg, synthetic_chain, busy_candidate, route_table, centrality_table, max_flight_count, max_betweenness,
        route_duration_table, airport_coords,
    )
    print(f"Idle candidate components: {idle_components}")
    print(f"Busy candidate components: {busy_components}")
    # BEFORE: busy opportunity_cost=149.0 (no return-trip charge). AFTER:
    # busy is charged a fallback return trip from fake 'D' back to MCO.

    for mode in RANKING_MODES:
        idle_score = replacement_score(idle_components, mode)
        busy_score = replacement_score(busy_components, mode)
        print(f"  {mode}: idle={idle_score}, busy={busy_score}")
        assert idle_score <= busy_score, (
            f"{mode}: fully idle candidate should never score worse than an otherwise-identical "
            f"busy candidate (idle={idle_score}, busy={busy_score})"
        )
    print("replacement_score: idle candidate never scores worse than busy, across all 5 modes.")

    # --- rank_candidates: real N101DQ / 2025-01-01 scenario, all_factors_combined ---
    # BEFORE this fix: all candidates had delay=0.0, opportunity_cost=0.0.
    ranked = rank_candidates(
        cancelled_leg_real, real_downstream, candidates, "all_factors_combined",
        route_table, centrality_table, legs_by_tail, route_duration_table, airport_coords,
    )
    print("Real N101DQ ranked candidates (all_factors_combined) -- AFTER fix:")
    for entry in ranked:
        print(f"  {entry['tail_number']}: score={entry['score']}, delay={entry['delay']}, "
              f"network_disruption={entry['network_disruption']}, "
              f"connecting_flights_missed={entry['connecting_flights_missed']}")

    # --- compute_recovery_improvement: real N101DQ / 2025-01-01 scenario ---
    # BEFORE this fix: improvement_pct=100.0 (opportunity_cost wasn't charged).
    real_improvement = compute_recovery_improvement(
        cancelled_leg_real, real_downstream, candidates, "all_factors_combined",
        route_table, centrality_table, legs_by_tail, route_duration_table, airport_coords,
    )
    print(f"Real N101DQ recovery improvement (all_factors_combined) -- AFTER fix: {real_improvement}")
    print(f"  baseline_cost_minutes={real_improvement['baseline_cost_minutes']}, "
          f"total_induced_delay_minutes={real_improvement['total_induced_delay_minutes']}")

    assert real_improvement["improvement_pct"] is not None and real_improvement["improvement_pct"] >= 0, (
        f"improvement_pct went negative ({real_improvement['improvement_pct']}) for a real scenario -- "
        f"this means either a scoring bug or CANCELLATION_PENALTY_MINUTES is set too low, "
        f"not a valid result to silently accept"
    )
    print("compute_recovery_improvement: improvement_pct is non-negative for the real N101DQ scenario.")

    # --- compute_recovery_improvement: synthetic empty-candidates edge case ---
    # candidates=[] short-circuits before rank_candidates is ever called.
    no_candidates_improvement = compute_recovery_improvement(
        cancelled_leg_real, real_downstream, [], "all_factors_combined",
        route_table, centrality_table, legs_by_tail, route_duration_table, airport_coords,
    )
    assert no_candidates_improvement["best_candidate"] is None, "expected no best_candidate when candidates is empty"
    assert no_candidates_improvement["total_induced_delay_minutes"] is None, (
        "expected no induced-delay figure when there's no candidate to simulate"
    )
    assert no_candidates_improvement["improvement_pct"] == 0.0, "expected 0% improvement when nothing can be done"
    print(f"Synthetic empty-candidates edge case: {no_candidates_improvement}")

    # ============================================================
    # Feature 2 (whole-aircraft-down): return_trip_estimate,
    # compute_segment_cost, assign_recovery_segments
    # (airport_coords/route_duration_table already built above, reused here)
    # ============================================================

    # --- return_trip_estimate: Tier 1, real route (MCO->SLC, N101DQ's own next leg) ---
    assert ('MCO', 'SLC') in route_duration_table, "expected a real MCO->SLC route in the duration table"
    mco_slc_estimate = return_trip_estimate('MCO', 'SLC', route_duration_table, airport_coords)
    assert mco_slc_estimate == route_duration_table[('MCO', 'SLC')], (
        "Tier 1 should return the route_duration_table average directly, unmodified"
    )
    print(f"\nreturn_trip_estimate('MCO', 'SLC') [Tier 1, real route]: {mco_slc_estimate:.1f} min")

    # --- return_trip_estimate: Tier 2, synthetic/rare pair with no real direct route ---
    # Search for a real airport pair with no direct scheduled route, rather than assuming one.
    coord_airports = sorted(airport_coords.keys())
    rare_pair = None
    for a in coord_airports:
        for b in coord_airports:
            if a != b and (a, b) not in route_duration_table:
                rare_pair = (a, b)
                break
        if rare_pair:
            break
    assert rare_pair is not None, "expected at least one real airport pair with no direct scheduled route"

    ferry_estimate = return_trip_estimate(rare_pair[0], rare_pair[1], route_duration_table, airport_coords)
    lat1, lon1 = airport_coords[rare_pair[0]]
    lat2, lon2 = airport_coords[rare_pair[1]]
    expected_distance_nm = great_circle_distance_nm(lat1, lon1, lat2, lon2)
    expected_ferry = (expected_distance_nm / CRUISE_SPEED_KTS) * 60 + TURNAROUND_BUFFER_MINUTES
    assert math.isclose(ferry_estimate, expected_ferry, rel_tol=1e-9), (
        f"Tier 2 ferry estimate mismatch: got {ferry_estimate}, expected {expected_ferry}"
    )
    assert ferry_estimate > 0, "ferry estimate should be a positive number of minutes"
    print(f"return_trip_estimate{rare_pair} [Tier 2, no real route, ferry fallback]: "
          f"{ferry_estimate:.1f} min ({expected_distance_nm:.0f} nm at {CRUISE_SPEED_KTS} kts + "
          f"{TURNAROUND_BUFFER_MINUTES} min turnaround buffer)")

    # --- assign_recovery_segments: real N101DQ / 2025-01-01, whole-aircraft-down at 10:30 ---
    segments_result = assign_recovery_segments(
        'N101DQ', '2025-01-01', '10:30', legs_by_tail, route_table, centrality_table,
        airport_coords, mode="all_factors_combined", route_duration_table=route_duration_table,
    )
    print(f"\nN101DQ whole-aircraft-down @ 2025-01-01 10:30 -- {segments_result['total_orphaned_legs']} orphaned legs:")
    for seg in segments_result['segments']:
        print(f"  tail={seg['tail_number']}, legs={seg['legs_covered']}, "
              f"induced_delay={seg['induced_delay_minutes']}, opportunity_cost={seg['opportunity_cost']}, "
              f"return_trip_minutes={seg['return_trip_minutes']}, score={seg['score']}")
    print(f"  totals: covered={segments_result['total_covered_legs']}, "
          f"uncovered={segments_result['total_uncovered_legs']}, "
          f"num_segments={segments_result['num_segments']}, "
          f"total_induced_delay_minutes={segments_result['total_induced_delay_minutes']}")

    assert segments_result['total_orphaned_legs'] == 3, (
        f"expected N101DQ's 3 remaining legs (MCO->SLC, SLC->MSP, MSP->BZN) orphaned, "
        f"got {segments_result['total_orphaned_legs']}"
    )
    orphaned_check = get_orphaned_legs(legs_by_tail, 'N101DQ', '2025-01-01', '10:30')
    assert [(u, v) for u, v, d in orphaned_check] == [('MCO', 'SLC'), ('SLC', 'MSP'), ('MSP', 'BZN')], (
        f"expected the exact chain already validated for N101DQ's own_remaining_legs, "
        f"got {[(u, v) for u, v, d in orphaned_check]}"
    )

    # Hand-verify the first segment's extend-or-stop decision: recompute both
    # options (stop at 1 leg vs. extend to 2) directly and confirm the
    # algorithm's choice matches whichever scores lower.
    first_segment = segments_result['segments'][0]
    assert first_segment['tail_number'] is not None, "expected the first segment to find a real candidate"

    first_leg_query_time = orphaned_check[0][2]['crs_dep_dt'].strftime('%H:%M')
    first_leg_candidates = find_swap_candidates(legs_by_tail, orphaned_check[0][0], '2025-01-01', first_leg_query_time)
    first_leg_candidates = [c for c in first_leg_candidates if c['tail_number'] != 'N101DQ']
    winning_candidate = next(c for c in first_leg_candidates if c['tail_number'] == first_segment['tail_number'])

    max_flight_count = route_table['flight_count'].max()
    max_betweenness = max(centrality_table.values())
    stop_at_1_components = compute_segment_cost(
        winning_candidate, orphaned_check[0:1], route_table, centrality_table,
        max_flight_count, max_betweenness, route_duration_table, airport_coords,
    )
    stop_at_1_score = replacement_score(stop_at_1_components, "all_factors_combined")
    extend_to_2_components = compute_segment_cost(
        winning_candidate, orphaned_check[0:2], route_table, centrality_table,
        max_flight_count, max_betweenness, route_duration_table, airport_coords,
    )
    extend_to_2_score = replacement_score(extend_to_2_components, "all_factors_combined")

    print(f"\nHand-verification for winning candidate {winning_candidate['tail_number']} on the first segment:")
    print(f"  stop at 1 leg (MCO->SLC only):          score={stop_at_1_score}")
    print(f"  extend to 2 legs (MCO->SLC, SLC->MSP):  score={extend_to_2_score}")

    chosen_length = len(first_segment['legs_covered'])
    if extend_to_2_score < stop_at_1_score:
        assert chosen_length >= 2, (
            f"extending scored cheaper ({extend_to_2_score} < {stop_at_1_score}) but the algorithm "
            f"only covered {chosen_length} leg(s) -- extend-or-stop decision disagrees with the hand check"
        )
        print(f"  extending was cheaper -- algorithm's segment length ({chosen_length}) is consistent with that.")
    else:
        assert chosen_length == 1, (
            f"stopping scored cheaper or equal ({stop_at_1_score} <= {extend_to_2_score}) but the algorithm "
            f"covered {chosen_length} legs -- extend-or-stop decision disagrees with the hand check"
        )
        print(f"  stopping at 1 leg was cheaper (or equal) -- algorithm's segment length ({chosen_length}) is consistent with that.")

    # --- N348NB Feature 1 (fixed) vs. Feature 2 parity check ---
    # For the same single-leg scenario, Feature 1's opportunity_cost should
    # now match compute_segment_cost's exactly. BEFORE this fix: 0.0.
    n348nb_candidate = next(c for c in first_leg_candidates if c['tail_number'] == 'N348NB')

    f1_fixed_components = compute_candidate_components(
        cancelled_leg_real, orphaned_check[0:1], n348nb_candidate, route_table, centrality_table,
        max_flight_count, max_betweenness, route_duration_table, airport_coords,
    )
    f2_components = compute_segment_cost(
        n348nb_candidate, orphaned_check[0:1], route_table, centrality_table,
        max_flight_count, max_betweenness, route_duration_table, airport_coords,
    )
    print("\nN348NB / MCO->SLC / 11:10-query-time -- Feature 1 (fixed) vs. Feature 2 parity:")
    print(f"  Feature 1 opportunity_cost -- BEFORE fix: 0.0, AFTER fix: {f1_fixed_components['opportunity_cost']}")
    print(f"  Feature 2 (compute_segment_cost) opportunity_cost: {f2_components['opportunity_cost']}")
    assert math.isclose(f1_fixed_components['opportunity_cost'], f2_components['opportunity_cost'], rel_tol=1e-9), (
        f"Feature 1 (fixed) and Feature 2 should compute IDENTICAL opportunity_cost for the same "
        f"single-leg candidate/segment -- got {f1_fixed_components['opportunity_cost']} vs "
        f"{f2_components['opportunity_cost']}"
    )
    assert f1_fixed_components['opportunity_cost'] > 1000, (
        "expected the fixed opportunity_cost to be large (same order of magnitude as the ~1460.59 "
        "minutes found during Feature 2 validation), not the old near-zero value"
    )
    print("  Feature 1 (fixed) and Feature 2 opportunity_cost match exactly, as expected.")

    # --- assign_recovery_segments: synthetic uncovered-leg edge case ---
    # DOWN1's RRR->SSS has no candidate (DOWN1 itself would otherwise be its
    # own only "candidate", testing self-exclusion); SSS->TTT has HELPER1 --
    # proves the algorithm marks the first leg uncovered and continues.
    synthetic_legs_by_tail = {
        'DOWN1': [
            ('QQQ', 'RRR', {
                'flight_date': '2025-06-01', 'cancelled': 0.0,
                'crs_dep_dt': pd.Timestamp('2025-06-01 07:30'), 'crs_arr_dt': pd.Timestamp('2025-06-01 08:30'),
                'dep_dt': pd.Timestamp('2025-06-01 07:35'), 'arr_dt': pd.Timestamp('2025-06-01 08:35'),
                'wheels_off': pd.Timestamp('2025-06-01 07:40'), 'wheels_on': pd.Timestamp('2025-06-01 08:30'),
                'turnaround_to_next': None,
            }),
            ('RRR', 'SSS', {
                'flight_date': '2025-06-01', 'cancelled': 0.0,
                'crs_dep_dt': pd.Timestamp('2025-06-01 10:00'), 'crs_arr_dt': pd.Timestamp('2025-06-01 11:00'),
                'dep_dt': pd.NaT, 'arr_dt': pd.NaT, 'wheels_off': pd.NaT, 'wheels_on': pd.NaT,
                'turnaround_to_next': None,
            }),
            ('SSS', 'TTT', {
                'flight_date': '2025-06-01', 'cancelled': 0.0,
                'crs_dep_dt': pd.Timestamp('2025-06-01 13:00'), 'crs_arr_dt': pd.Timestamp('2025-06-01 14:00'),
                'dep_dt': pd.NaT, 'arr_dt': pd.NaT, 'wheels_off': pd.NaT, 'wheels_on': pd.NaT,
                'turnaround_to_next': None,
            }),
        ],
        'HELPER1': [
            ('XXX', 'SSS', {
                'flight_date': '2025-06-01', 'cancelled': 0.0,
                'crs_dep_dt': pd.Timestamp('2025-06-01 11:00'), 'crs_arr_dt': pd.Timestamp('2025-06-01 12:00'),
                'dep_dt': pd.Timestamp('2025-06-01 11:05'), 'arr_dt': pd.Timestamp('2025-06-01 12:00'),
                'wheels_off': pd.Timestamp('2025-06-01 11:10'), 'wheels_on': pd.Timestamp('2025-06-01 11:55'),
                'turnaround_to_next': None,
            }),
        ],
    }

    synthetic_result = assign_recovery_segments(
        'DOWN1', '2025-06-01', '09:00', synthetic_legs_by_tail, route_table, centrality_table,
        airport_coords, mode="all_factors_combined",
    )
    print("\nSynthetic uncovered-leg edge case -- DOWN1 whole-aircraft-down @ 2025-06-01 09:00:")
    for seg in synthetic_result['segments']:
        print(f"  tail={seg['tail_number']}, legs={seg['legs_covered']}, score={seg['score']}")

    assert synthetic_result['total_orphaned_legs'] == 2, (
        f"expected 2 orphaned legs (RRR->SSS, SSS->TTT), got {synthetic_result['total_orphaned_legs']}"
    )
    assert len(synthetic_result['segments']) == 2, "expected two segments: one uncovered, one covered"
    assert synthetic_result['segments'][0]['tail_number'] is None, (
        "expected RRR->SSS to be marked uncovered (DOWN1 is its own only candidate and must be excluded)"
    )
    assert synthetic_result['segments'][0]['legs_covered'] == [('RRR', 'SSS')]
    assert synthetic_result['segments'][1]['tail_number'] == 'HELPER1', (
        "expected the algorithm to continue past the uncovered leg and find HELPER1 for SSS->TTT"
    )
    assert synthetic_result['segments'][1]['legs_covered'] == [('SSS', 'TTT')]
    assert synthetic_result['total_uncovered_legs'] == 1 and synthetic_result['total_covered_legs'] == 1
    print("assign_recovery_segments: uncovered leg correctly marked and search continued to a real "
          "segment afterward, without crashing.")

    # ============================================================
    # Part 1: greedy candidate-reuse fix (claimed set)
    # ============================================================

    # --- real N101DQ scenario is unaffected -- no collision ever existed here ---
    assert [seg['tail_number'] for seg in segments_result['segments']] == ['N111DC', 'N375DA', 'N109DN'], (
        f"expected the claimed-set fix to be a no-op for the real N101DQ scenario (no reuse ever "
        f"occurred here), got {[seg['tail_number'] for seg in segments_result['segments']]}"
    )
    for seg, expected_score in zip(
        segments_result['segments'], [330.8601239117239, 9.291133257972188, 7.303169605357857]
    ):
        assert math.isclose(seg['score'], expected_score, rel_tol=1e-9), (
            f"expected {seg['tail_number']}'s score to match the pre-fix-validation recorded value "
            f"{expected_score}, got {seg['score']}"
        )
    print("\nPart 1 fix: real N101DQ scenario byte-identical to the recorded pre-fix result "
          "(no collision existed here, so the claimed-set exclusion is correctly a no-op).")

    # --- synthetic candidate-reuse collision, engineered against OLD (pre-fix) logic ---
    # DOWN2's legs are O1->O2 and O2->O3. HELPER2 is parked at O1 before leg 1's
    # query time (segment 1's only candidate) and, because it independently
    # flies O1->O2 itself, also parked at O2 before leg 2's query time
    # (segment 2's only candidate too) -- without the claimed-set fix it
    # would be recommended for both segments at once.
    collision_legs_by_tail = {
        'DOWN2': [
            ('AAA', 'O1', {
                'flight_date': '2025-07-01', 'cancelled': 0.0,
                'crs_dep_dt': pd.Timestamp('2025-07-01 07:30'), 'crs_arr_dt': pd.Timestamp('2025-07-01 08:30'),
                'dep_dt': pd.Timestamp('2025-07-01 07:35'), 'arr_dt': pd.Timestamp('2025-07-01 08:35'),
                'wheels_off': pd.Timestamp('2025-07-01 07:40'), 'wheels_on': pd.Timestamp('2025-07-01 08:30'),
                'turnaround_to_next': None,
            }),
            ('O1', 'O2', {
                'flight_date': '2025-07-01', 'cancelled': 0.0,
                'crs_dep_dt': pd.Timestamp('2025-07-01 10:00'), 'crs_arr_dt': pd.Timestamp('2025-07-01 11:00'),
                'dep_dt': pd.NaT, 'arr_dt': pd.NaT, 'wheels_off': pd.NaT, 'wheels_on': pd.NaT,
                'turnaround_to_next': None,
            }),
            ('O2', 'O3', {
                'flight_date': '2025-07-01', 'cancelled': 0.0,
                'crs_dep_dt': pd.Timestamp('2025-07-01 13:00'), 'crs_arr_dt': pd.Timestamp('2025-07-01 14:00'),
                'dep_dt': pd.NaT, 'arr_dt': pd.NaT, 'wheels_off': pd.NaT, 'wheels_on': pd.NaT,
                'turnaround_to_next': None,
            }),
        ],
        'HELPER2': [
            ('XXX', 'O1', {
                'flight_date': '2025-07-01', 'cancelled': 0.0,
                'crs_dep_dt': pd.Timestamp('2025-07-01 08:00'), 'crs_arr_dt': pd.Timestamp('2025-07-01 09:00'),
                'dep_dt': pd.Timestamp('2025-07-01 08:05'), 'arr_dt': pd.Timestamp('2025-07-01 09:00'),
                'wheels_off': pd.Timestamp('2025-07-01 08:10'), 'wheels_on': pd.Timestamp('2025-07-01 08:55'),
                'turnaround_to_next': None,
            }),
            ('O1', 'O2', {
                'flight_date': '2025-07-01', 'cancelled': 0.0,
                'crs_dep_dt': pd.Timestamp('2025-07-01 10:30'), 'crs_arr_dt': pd.Timestamp('2025-07-01 11:30'),
                'dep_dt': pd.Timestamp('2025-07-01 10:35'), 'arr_dt': pd.Timestamp('2025-07-01 11:30'),
                'wheels_off': pd.Timestamp('2025-07-01 10:40'), 'wheels_on': pd.Timestamp('2025-07-01 11:25'),
                'turnaround_to_next': None,
            }),
        ],
    }

    # Confirm the collision precondition: HELPER2's raw pool (pre-claimed-set)
    # independently includes it for both segments' searches.
    seg1_raw_candidates = find_swap_candidates(collision_legs_by_tail, 'O1', '2025-07-01', '10:00')
    seg2_raw_candidates = find_swap_candidates(collision_legs_by_tail, 'O2', '2025-07-01', '13:00')
    seg1_tails = {c['tail_number'] for c in seg1_raw_candidates}
    seg2_tails = {c['tail_number'] for c in seg2_raw_candidates}
    assert 'HELPER2' in seg1_tails and 'HELPER2' in seg2_tails, (
        f"fixture didn't force the intended collision precondition -- expected HELPER2 in both raw "
        f"pools, got seg1={seg1_tails}, seg2={seg2_tails}"
    )
    print(f"\nPart 1 fix: collision fixture -- HELPER2 independently appears in BOTH segments' raw "
          f"candidate pools (seg1@O1/10:00={seg1_tails}, seg2@O2/13:00={seg2_tails}), confirming the "
          f"collision the old code would have produced.")

    collision_result = assign_recovery_segments(
        'DOWN2', '2025-07-01', '09:00', collision_legs_by_tail, route_table, centrality_table,
        airport_coords, mode="all_factors_combined",
    )
    print("Part 1 fix: DOWN2 whole-aircraft-down @ 2025-07-01 09:00 (post-fix):")
    for seg in collision_result['segments']:
        print(f"  tail={seg['tail_number']}, legs={seg['legs_covered']}, score={seg['score']}")

    assigned_tails = [seg['tail_number'] for seg in collision_result['segments'] if seg['tail_number'] is not None]
    assert assigned_tails.count('HELPER2') == 1, (
        f"expected HELPER2 to be claimed for exactly one segment, got it assigned "
        f"{assigned_tails.count('HELPER2')} times: {collision_result['segments']}"
    )
    assert collision_result['segments'][0]['tail_number'] == 'HELPER2', (
        "expected HELPER2 to cover segment 1 (O1->O2)"
    )
    assert collision_result['segments'][1]['tail_number'] is None, (
        f"expected segment 2 (O2->O3) to be uncovered post-fix (HELPER2, its only candidate, is "
        f"already claimed) -- got {collision_result['segments'][1]}"
    )
    print("Part 1 fix confirmed: HELPER2 claimed for segment 1 only; segment 2 correctly falls back "
          "to uncovered instead of double-booking the same tail.")

    # ============================================================
    # Part 2: optimal DP/shortest-path solver vs. fixed greedy
    # ============================================================

    def total_path_cost(result):
        covered = [s for s in result['segments'] if s['tail_number'] is not None]
        uncovered = [s for s in result['segments'] if s['tail_number'] is None]
        uncovered_legs = sum(len(s['legs_covered']) for s in uncovered)
        return sum(s['score'] for s in covered) + CANCELLATION_PENALTY_MINUTES * uncovered_legs

    def leg_assignment(result):
        mapping = {}
        for seg in result['segments']:
            for leg in seg['legs_covered']:
                mapping[leg] = seg['tail_number']
        return mapping

    def print_segments(label, result):
        print(f"  {label}:")
        for seg in result['segments']:
            print(f"    tail={seg['tail_number']}, legs={seg['legs_covered']}, score={seg['score']}")

    # --- scenario A: real N101DQ / 2025-01-01, same scenario as Part 1 ---
    optimal_result = assign_recovery_segments_optimal(
        'N101DQ', '2025-01-01', '10:30', legs_by_tail, route_table, centrality_table,
        airport_coords, mode="all_factors_combined", route_duration_table=route_duration_table,
    )
    greedy_total = total_path_cost(segments_result)
    optimal_total = total_path_cost(optimal_result)
    print("\nPart 2: N101DQ / 2025-01-01 @ 10:30 (3 orphaned legs) -- optimal vs. fixed-greedy:")
    print(f"  fixed-greedy total_path_cost = {greedy_total}")
    print_segments("fixed-greedy segments", segments_result)
    print(f"  optimal total_path_cost      = {optimal_total}")
    print_segments("optimal segments", optimal_result)
    print(f"  gap (greedy - optimal) = {greedy_total - optimal_total}")
    assert optimal_total <= greedy_total + 1e-9, (
        f"optimal solver should never do worse than greedy, got optimal={optimal_total} > greedy={greedy_total}"
    )

    scenario_a_map_greedy = leg_assignment(segments_result)
    scenario_a_map_optimal = leg_assignment(optimal_result)
    if scenario_a_map_greedy != scenario_a_map_optimal:
        print("  segment assignments DIVERGE -- per-leg diff:")
        for leg_key, greedy_tail in scenario_a_map_greedy.items():
            optimal_tail = scenario_a_map_optimal[leg_key]
            if greedy_tail != optimal_tail:
                print(f"    leg {leg_key}: greedy->{greedy_tail}, optimal->{optimal_tail}")
    else:
        print("  segment assignments are identical between greedy and optimal.")

    # --- scenario B: a real scenario with MORE orphaned legs than N101DQ's 3 ---
    # Found programmatically (first match, sorted for reproducibility): a
    # busy day where, probed right after the first leg lands, >3 legs remain.
    big_scenario = None
    for candidate_tail in sorted(legs_by_tail):
        by_date = {}
        for u, v, d in legs_by_tail[candidate_tail]:
            by_date.setdefault(d['flight_date'], []).append((u, v, d))
        for flight_date, day_legs in sorted(by_date.items()):
            if len(day_legs) < 5:
                continue
            day_legs = sorted(day_legs, key=lambda e: e[2]['crs_dep_dt'])
            first_leg = day_legs[0]
            if pd.isna(first_leg[2].get('arr_dt')):
                continue
            probe_query_time = first_leg[2]['arr_dt'].strftime('%H:%M')
            orphaned = get_orphaned_legs(legs_by_tail, candidate_tail, flight_date, probe_query_time)
            if len(orphaned) > 3:
                big_scenario = (candidate_tail, flight_date, probe_query_time, len(orphaned))
                break
        if big_scenario:
            break

    assert big_scenario is not None, "expected at least one real tail/date with more than 3 orphaned legs"
    big_tail, big_date, big_query_time, big_orphaned_count = big_scenario
    print(f"\nPart 2 scenario B (found by search): tail={big_tail}, date={big_date}, "
          f"query_time={big_query_time}, orphaned_legs={big_orphaned_count}")

    big_greedy = assign_recovery_segments(
        big_tail, big_date, big_query_time, legs_by_tail, route_table, centrality_table,
        airport_coords, mode="all_factors_combined", route_duration_table=route_duration_table,
    )
    big_optimal = assign_recovery_segments_optimal(
        big_tail, big_date, big_query_time, legs_by_tail, route_table, centrality_table,
        airport_coords, mode="all_factors_combined", route_duration_table=route_duration_table,
    )
    big_greedy_total = total_path_cost(big_greedy)
    big_optimal_total = total_path_cost(big_optimal)
    print(f"  fixed-greedy total_path_cost = {big_greedy_total}")
    print_segments("fixed-greedy segments", big_greedy)
    print(f"  optimal total_path_cost      = {big_optimal_total}")
    print_segments("optimal segments", big_optimal)
    print(f"  gap (greedy - optimal) = {big_greedy_total - big_optimal_total}")
    assert big_optimal_total <= big_greedy_total + 1e-9, (
        f"optimal solver should never do worse than greedy on scenario B, "
        f"got optimal={big_optimal_total} > greedy={big_greedy_total}"
    )

    scenario_b_map_greedy = leg_assignment(big_greedy)
    scenario_b_map_optimal = leg_assignment(big_optimal)
    if scenario_b_map_greedy != scenario_b_map_optimal:
        print("  segment assignments DIVERGE -- per-leg diff:")
        for leg_key, greedy_tail in scenario_b_map_greedy.items():
            optimal_tail = scenario_b_map_optimal[leg_key]
            if greedy_tail != optimal_tail:
                print(f"    leg {leg_key}: greedy->{greedy_tail}, optimal->{optimal_tail}")
    else:
        print("  segment assignments are identical between greedy and optimal.")

    print("\nAll disruption.py sanity checks passed.")