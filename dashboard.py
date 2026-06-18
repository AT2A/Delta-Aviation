import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import airportsdata

CSV_PATH = "data/delta_ontime.csv"

COLUMNS = [
    "Year", "Month", "DayofMonth", "DayOfWeek", "FlightDate",
    "Reporting_Airline", "Tail_Number", "Flight_Number_Reporting_Airline",
    "Origin", "OriginCityName", "Dest", "DestCityName",
    "CRSDepTime", "DepTime", "DepDelayMinutes",
    "CRSArrTime", "ArrTime", "ArrDelayMinutes",
    "Cancelled", "CancellationCode", "Diverted",
    "Distance", "AirTime",
    "CarrierDelay", "WeatherDelay", "NASDelay", "SecurityDelay", "LateAircraftDelay",
]

st.set_page_config(page_title="Flight On-Time Dashboard", layout="wide")


@st.cache_data
def load_data():
    df = pd.read_csv(CSV_PATH, usecols=COLUMNS, low_memory=False)
    df["FlightDate"] = pd.to_datetime(df["FlightDate"])
    return df


@st.cache_data
def airport_coords():
    return airportsdata.load("IATA")


def delay_color(delay, vmin=-5, vmax=30):
    if pd.isna(delay):
        return "rgb(150,150,150)"
    frac = np.clip((delay - vmin) / (vmax - vmin), 0, 1)
    r = int(255 * frac)
    g = int(255 * (1 - frac))
    return f"rgb({r},{g},40)"


df = load_data()
airports = airport_coords()

data_start = df["FlightDate"].min().strftime("%b %Y")
data_end = df["FlightDate"].max().strftime("%b %Y")

st.title("Delta Air Lines — On-Time Performance Dashboard")
st.caption(f"BTS Carrier On-Time Performance — Delta (DL) only, {data_start} to {data_end}")

# ── Sidebar filters ───────────────────────────────────────────────────────
st.sidebar.header("Filters")

min_date, max_date = df["FlightDate"].min().date(), df["FlightDate"].max().date()
date_range = st.sidebar.slider(
    "Date range", min_value=min_date, max_value=max_date, value=(min_date, max_date)
)

all_origins = sorted(set(df["Origin"]) | set(df["Dest"]))
sel_origin = st.sidebar.multiselect("Origin airport", all_origins)
sel_dest = st.sidebar.multiselect("Destination airport", all_origins)

status = st.sidebar.radio("Flight status", ["All", "Completed only", "Cancelled only", "Diverted only"])

max_routes = st.sidebar.slider("Max routes shown on map", 10, 500, 150, step=10)

# ── Apply filters ─────────────────────────────────────────────────────────
mask = df["FlightDate"].dt.date.between(date_range[0], date_range[1])
if sel_origin:
    mask &= df["Origin"].isin(sel_origin)
if sel_dest:
    mask &= df["Dest"].isin(sel_dest)
if status == "Completed only":
    mask &= (df["Cancelled"] == 0) & (df["Diverted"] == 0)
elif status == "Cancelled only":
    mask &= df["Cancelled"] == 1
elif status == "Diverted only":
    mask &= df["Diverted"] == 1

fdf = df[mask]

# ── Top metrics ───────────────────────────────────────────────────────────
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Flights", f"{len(fdf):,}")
c2.metric("Cancelled", f"{(fdf['Cancelled'] == 1).mean() * 100:.1f}%")
c3.metric("Diverted", f"{(fdf['Diverted'] == 1).mean() * 100:.1f}%")
c4.metric("Avg dep delay (min)", f"{fdf['DepDelayMinutes'].mean():.1f}")
c5.metric("Avg arr delay (min)", f"{fdf['ArrDelayMinutes'].mean():.1f}")

# ── Route map ─────────────────────────────────────────────────────────────
st.subheader("Route Map")

flown = fdf[fdf["Cancelled"] == 0]

routes = (
    flown.groupby(["Origin", "Dest"])
    .agg(
        flights=("Origin", "size"),
        avg_arr_delay=("ArrDelayMinutes", "mean"),
        avg_dep_delay=("DepDelayMinutes", "mean"),
    )
    .reset_index()
    .sort_values("flights", ascending=False)
)

shown_routes = routes.head(max_routes)

origin_counts = flown["Origin"].value_counts()
dest_counts = flown["Dest"].value_counts()
airport_traffic = origin_counts.add(dest_counts, fill_value=0).astype(int)

# Average of departure delay (as origin) and arrival delay (as dest) per airport
dep_by_airport = flown.groupby("Origin")["DepDelayMinutes"].mean()
arr_by_airport = flown.groupby("Dest")["ArrDelayMinutes"].mean()
airport_delay = pd.concat([dep_by_airport, arr_by_airport], axis=1).mean(axis=1, skipna=True)

fig = go.Figure()

missing_codes = set()
for _, r in shown_routes.iterrows():
    o, d = airports.get(r["Origin"]), airports.get(r["Dest"])
    if o is None:
        missing_codes.add(r["Origin"])
        continue
    if d is None:
        missing_codes.add(r["Dest"])
        continue
    fig.add_trace(go.Scattergeo(
        lat=[o["lat"], d["lat"]],
        lon=[o["lon"], d["lon"]],
        mode="lines",
        line=dict(
            width=1 + 3 * (r["flights"] / shown_routes["flights"].max()),
            color=delay_color(r["avg_arr_delay"]),
        ),
        opacity=0.5,
        hoverinfo="text",
        text=(
            f"{r['Origin']} → {r['Dest']}<br>"
            f"Flights: {r['flights']:,}<br>"
            f"Avg dep delay: {r['avg_dep_delay']:.1f} min<br>"
            f"Avg arr delay: {r['avg_arr_delay']:.1f} min"
        ),
        showlegend=False,
    ))

lats, lons, labels, sizes, marker_colors = [], [], [], [], []
for code, count in airport_traffic.items():
    info = airports.get(code)
    if info is None:
        missing_codes.add(code)
        continue
    lats.append(info["lat"])
    lons.append(info["lon"])
    sizes.append(count)
    marker_colors.append(airport_delay.get(code, np.nan))
    labels.append(
        f"<b>{code}</b> — {info.get('name', 'Unknown')}"
        f"<br>{info.get('city', '')}, {info.get('subd', '')}"
        f"<br>Flights: {count:,}"
        f"<br>Avg delay: {airport_delay.get(code, float('nan')):.1f} min"
    )

sqrt_sizes = np.sqrt(np.array(sizes, dtype=float))
marker_sizes = 4 + 26 * (sqrt_sizes - sqrt_sizes.min()) / (sqrt_sizes.max() - sqrt_sizes.min() + 1e-9)

fig.add_trace(go.Scattergeo(
    lat=lats,
    lon=lons,
    text=labels,
    hoverinfo="text",
    mode="markers",
    marker=dict(
        size=marker_sizes,
        color=marker_colors,
        colorscale="RdYlGn_r",
        cmin=-5,
        cmax=30,
        colorbar=dict(title="Avg delay (min)", thickness=15, len=0.6),
        line=dict(width=0.4, color="black"),
        opacity=0.9,
    ),
    name="Airports",
))

fig.update_layout(
    geo=dict(
        scope="usa",
        projection_type="albers usa",
        showland=True,
        landcolor="rgb(235, 235, 235)",
        showlakes=True,
        lakecolor="rgb(190, 215, 240)",
        showcoastlines=True,
    ),
    margin=dict(l=0, r=0, t=10, b=0),
    height=600,
)

st.plotly_chart(fig, use_container_width=True)
st.caption(
    f"Showing top {len(shown_routes)} of {len(routes):,} routes by flight count. "
    f"Marker size = traffic, marker/line color = avg delay (green = early, red = late)."
)
if missing_codes:
    st.caption(f"Could not place {len(missing_codes)} airport code(s): {', '.join(sorted(missing_codes))}")

# ── Charts ────────────────────────────────────────────────────────────────
st.subheader("Delays by Month")
by_month = (
    flown.assign(Month=flown["FlightDate"].dt.to_period("M").dt.to_timestamp())
    .groupby("Month")
    .agg(
        flights=("Month", "size"),
        avg_dep_delay=("DepDelayMinutes", "mean"),
        avg_arr_delay=("ArrDelayMinutes", "mean"),
    )
    .reset_index()
    .sort_values("Month")
)
st.bar_chart(by_month.set_index("Month")[["avg_dep_delay", "avg_arr_delay"]])

st.subheader("Flights by Day")
by_day = fdf.groupby(fdf["FlightDate"].dt.date).size()
st.line_chart(by_day)

# ── Flight table ──────────────────────────────────────────────────────────
st.subheader("Flight Detail")

search = st.text_input("Search by tail number or flight number")
table_df = fdf
if search:
    table_df = table_df[
        table_df["Tail_Number"].astype(str).str.contains(search, case=False, na=False)
        | table_df["Flight_Number_Reporting_Airline"].astype(str).str.contains(search, case=False, na=False)
    ]

display_cols = [
    "FlightDate", "Reporting_Airline", "Flight_Number_Reporting_Airline", "Tail_Number",
    "Origin", "Dest", "CRSDepTime", "DepTime", "DepDelayMinutes",
    "CRSArrTime", "ArrTime", "ArrDelayMinutes", "Cancelled", "Diverted", "Distance",
]
st.dataframe(table_df[display_cols].head(1000), use_container_width=True)
st.caption(f"Showing first 1,000 of {len(table_df):,} matching flights.")

# ── Aircraft Tracker ──────────────────────────────────────────────────────
st.subheader("Aircraft Tracker")
st.caption(
    "Follow a single physical aircraft (tail number) through every leg it flew on a given day. "
    "Change the date to see how the routing shifts — aircraft schedules vary daily."
)


@st.cache_data
def busiest_tails_by_day(df):
    non_cancel = df[df["Cancelled"] != 1]
    daily = (
        non_cancel.groupby(["Tail_Number", "FlightDate"])
        .size()
        .reset_index(name="n_legs")
    )
    idx = daily.groupby("Tail_Number")["n_legs"].idxmax()
    return daily.loc[idx].sort_values("n_legs", ascending=False).reset_index(drop=True).head(100)


top_tails = busiest_tails_by_day(df)

tail_labels = [
    f"{row['Tail_Number']}  —  {int(row['n_legs'])} legs on {row['FlightDate'].strftime('%b %d')}"
    for _, row in top_tails.iterrows()
]
sel_tail_label = st.selectbox("Aircraft  (top 100 by busiest single day)", tail_labels, index=0)
sel_tail = sel_tail_label.split("  —")[0].strip()

tail_df = df[df["Tail_Number"] == sel_tail].copy()
avail_dates = sorted(tail_df["FlightDate"].dt.date.unique())
busiest_date = top_tails[top_tails["Tail_Number"] == sel_tail]["FlightDate"].iloc[0].date()
default_date_idx = avail_dates.index(busiest_date) if busiest_date in avail_dates else 0
sel_date = st.selectbox(
    f"Date  ({len(avail_dates)} flying days in dataset for this aircraft)",
    avail_dates,
    index=default_date_idx,
)

day_df = (
    tail_df[tail_df["FlightDate"].dt.date == sel_date]
    .sort_values("CRSDepTime")
    .reset_index(drop=True)
)

completed_legs = day_df[day_df["Cancelled"] != 1]
n_cancelled = int((day_df["Cancelled"] == 1).sum())
total_dist = completed_legs["Distance"].sum()
avg_arr_delay = completed_legs["ArrDelayMinutes"].mean()

ac1, ac2, ac3, ac4, ac5 = st.columns(5)
ac1.metric("Legs scheduled", len(day_df))
ac2.metric("Completed", len(completed_legs))
ac3.metric("Cancelled", n_cancelled)
ac4.metric("Total distance", f"{int(total_dist):,} mi" if pd.notna(total_dist) and total_dist > 0 else "—")
ac5.metric("Avg arr delay", f"{avg_arr_delay:.1f} min" if pd.notna(avg_arr_delay) else "—")


def fmt_hhmm(t):
    if pd.isna(t):
        return "—"
    t = int(t)
    return f"{t // 100:02d}:{t % 100:02d}"


fig_track = go.Figure()

for i, (_, leg) in enumerate(day_df.iterrows()):
    o_info = airports.get(leg["Origin"])
    d_info = airports.get(leg["Dest"])
    if o_info is None or d_info is None:
        continue

    is_cancelled = leg["Cancelled"] == 1
    line_color = "rgb(170,170,170)" if is_cancelled else delay_color(leg["ArrDelayMinutes"])

    flt_num = str(int(leg["Flight_Number_Reporting_Airline"])) if pd.notna(leg["Flight_Number_Reporting_Airline"]) else "—"
    dep_d = f"{int(leg['DepDelayMinutes']):+d} min" if pd.notna(leg["DepDelayMinutes"]) else "—"
    arr_d = f"{int(leg['ArrDelayMinutes']):+d} min" if pd.notna(leg["ArrDelayMinutes"]) else "—"
    dist_str = f"{int(leg['Distance']):,} mi" if pd.notna(leg["Distance"]) else "—"

    if is_cancelled:
        hover = (
            f"Leg {i + 1}: {leg['Origin']} → {leg['Dest']}<br>"
            f"Flight: {leg['Reporting_Airline']}{flt_num}<br>"
            f"<b>CANCELLED</b>"
        )
    else:
        hover = (
            f"Leg {i + 1}: {leg['Origin']} → {leg['Dest']}<br>"
            f"Flight: {leg['Reporting_Airline']}{flt_num}<br>"
            f"Dep: {fmt_hhmm(leg['CRSDepTime'])} sched / {fmt_hhmm(leg['DepTime'])} act  ({dep_d})<br>"
            f"Arr: {fmt_hhmm(leg['CRSArrTime'])} sched / {fmt_hhmm(leg['ArrTime'])} act  ({arr_d})<br>"
            f"Distance: {dist_str}"
        )

    fig_track.add_trace(go.Scattergeo(
        lat=[o_info["lat"], d_info["lat"]],
        lon=[o_info["lon"], d_info["lon"]],
        mode="lines",
        line=dict(width=2.5, color=line_color, dash="dot" if is_cancelled else "solid"),
        hoverinfo="text",
        text=hover,
        showlegend=False,
    ))

# Build ordered stop list: origin of leg 0, then dest of every leg
stops = [(0, day_df.iloc[0]["Origin"])] if len(day_df) > 0 else []
for i, (_, leg) in enumerate(day_df.iterrows()):
    stops.append((i + 1, leg["Dest"]))

for seq, code in stops:
    info = airports.get(code)
    if info is None:
        continue
    is_start = seq == 0
    is_end = seq == len(stops) - 1
    marker_color = "gold" if is_start else ("tomato" if is_end else "steelblue")
    label = "S" if is_start else ("E" if is_end else str(seq))
    size = 15 if (is_start or is_end) else 12

    fig_track.add_trace(go.Scattergeo(
        lat=[info["lat"]],
        lon=[info["lon"]],
        mode="markers+text",
        marker=dict(size=size, color=marker_color, line=dict(width=1.5, color="white")),
        text=[label],
        textfont=dict(color="black" if is_start else "white", size=9, family="Arial Black"),
        textposition="middle center",
        hoverinfo="text",
        hovertext=(
            f"<b>{code}</b> — stop {seq}<br>"
            f"{info.get('name', '')}<br>"
            f"{info.get('city', '')}, {info.get('subd', '')}"
        ),
        showlegend=False,
    ))

fig_track.update_layout(
    geo=dict(
        scope="usa",
        projection_type="albers usa",
        showland=True,
        landcolor="rgb(235, 235, 235)",
        showlakes=True,
        lakecolor="rgb(190, 215, 240)",
        showcoastlines=True,
    ),
    margin=dict(l=0, r=0, t=10, b=0),
    height=480,
)

st.plotly_chart(fig_track, use_container_width=True)
st.caption(
    "S = first departure, E = final arrival, numbers = stop order.  "
    "Line color: green = on time / early, red = delayed (same scale as route map above).  "
    "Dotted line = cancelled leg."
)

# Leg detail table
rows = []
for i, (_, leg) in enumerate(day_df.iterrows()):
    flt_num = str(int(leg["Flight_Number_Reporting_Airline"])) if pd.notna(leg["Flight_Number_Reporting_Airline"]) else "—"
    dep_delay = f"{int(leg['DepDelayMinutes']):+d}" if pd.notna(leg["DepDelayMinutes"]) else "—"
    arr_delay = f"{int(leg['ArrDelayMinutes']):+d}" if pd.notna(leg["ArrDelayMinutes"]) else "—"
    dist = f"{int(leg['Distance']):,}" if pd.notna(leg["Distance"]) else "—"
    if leg["Cancelled"] == 1:
        status = "Cancelled"
    elif leg["Diverted"] == 1:
        status = "Diverted"
    else:
        status = "Completed"
    rows.append({
        "#": i + 1,
        "Route": f"{leg['Origin']} → {leg['Dest']}",
        "Flight": f"{leg['Reporting_Airline']}{flt_num}",
        "Sched Dep": fmt_hhmm(leg["CRSDepTime"]),
        "Act Dep": fmt_hhmm(leg["DepTime"]),
        "Dep Δ (min)": dep_delay,
        "Sched Arr": fmt_hhmm(leg["CRSArrTime"]),
        "Act Arr": fmt_hhmm(leg["ArrTime"]),
        "Arr Δ (min)": arr_delay,
        "Dist (mi)": dist,
        "Status": status,
    })

st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

unique_patterns = (
    tail_df[tail_df["Cancelled"] != 1]
    .groupby("FlightDate")
    .apply(lambda g: "|".join(
        g.sort_values("CRSDepTime").apply(lambda r: f"{r['Origin']}-{r['Dest']}", axis=1).tolist()
    ))
    .nunique()
)
st.caption(
    f"{sel_tail} flew on {len(avail_dates)} day{'s' if len(avail_dates) != 1 else ''} "
    f"between {data_start} and {data_end} "
    f"with {unique_patterns} unique routing pattern{'s' if unique_patterns != 1 else ''} — "
    "routes do change day to day."
)
