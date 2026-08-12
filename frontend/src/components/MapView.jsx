import DeckGL from "@deck.gl/react"
import { Map } from "react-map-gl/maplibre"
import { ScatterplotLayer, PathLayer, TextLayer } from "@deck.gl/layers"
import { useState, useEffect, useMemo, memo } from "react"
import "maplibre-gl/dist/maplibre-gl.css"
import { useTheme } from "../ThemeContext"

const INITIAL_VIEW = {
  longitude: -98.5,
  latitude: 39.5,
  zoom: 4,
}

// Volume-based visual weight for the "routes" layer -- suppresses the long
// tail of minor routes so major hub routes aren't lost in the haze.
const ROUTE_WIDTH_PX = [0.5, 5]
const ROUTE_OPACITY = [20, 200]
const lerp = ([lo, hi], t) => lo + t * (hi - lo)

function hexToRgb(hex) {
  const n = Number.parseInt(hex.replace("#", ""), 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

function scoreRoutes(routes) {
  const counts = routes.map(r => r.flight_count ?? 0)
  const min = Math.min(...counts)
  const max = Math.max(...counts)
  const span = max - min || 1
  return routes
    .map(r => ({ ...r, score: Math.pow(Math.max((r.flight_count ?? 0) - min, 0) / span, 1.2) }))
    .sort((a, b) => a.score - b.score) // hubs drawn last/on top
}

// Routes are static for the whole session -- they never change while the
// app is running -- but MapView unmounts/remounts on every navigation
// (React Router fully tears down page components), so without this, both
// the ~35-50MB /routes fetch and the scoreRoutes() pass over it re-ran from
// scratch on every visit to Overview or Live Replay, including navigating
// away and back. Cached at module scope (survives unmount) instead of in
// component state: the first caller kicks off the fetch+process and every
// later caller (this mount or a future one) gets the same resolved promise.
let routesCache = null
function getRoutesData() {
  if (!routesCache) {
    routesCache = fetch("/routes")
      .then(res => res.json())
      .then(data => scoreRoutes(data.routes.map(r => ({
        ...r,
        // Flip [lat, lon] -> [lon, lat] once here instead of per-render
        // inside getPath (which deck.gl would otherwise re-run on every
        // route on every re-render, including ones where routes hasn't
        // actually changed).
        path: r.path ? r.path.map(([lat, lon]) => [lon, lat]) : r.path,
      }))))
  }
  return routesCache
}

// aircraft status -> dot color
const STATUS_COLOR = {
  in_flight: [80, 200, 120],
  taxiing_out: [230, 190, 60],
  taxiing_in: [230, 190, 60],
  parked: [140, 150, 160],
  not_yet_started: [90, 110, 200],
  cancelled: [220, 60, 60],
  not_operating: [70, 75, 85],
}

// aircraft (optional): [{ tail_number, lat, lon, status }]
// colorRevision: opaque, at-most-once-a-second change signal for the
// aircraft layer's getFillColor updateTrigger (Live.jsx's searchSnapshot)
// -- not itself read for any color logic, just something that changes far
// less often than `aircraft` (a new array reference every animation frame
// during playback, since status can in principle change any frame) so
// deck.gl doesn't reprocess the fill-color attribute for all ~1000 aircraft
// 60x/sec when only position, not color, actually changes that often.
function MapView({ aircraft, onAircraftClick, height = "100vh", selectedAirport = null, colorRevision = null }) {
  const { theme } = useTheme()
  const [airports, setAirports] = useState([])
  const [routes, setRoutes] = useState([])
  // Defaults to a cap, not null/"show all" -- a real Chrome Performance
  // trace this session showed the GPU spending 83% of frame time
  // rasterizing this layer's ~23,409 alpha-blended, overlapping lines every
  // single frame (re-triggered whenever the aircraft layer changes, i.e.
  // every animation frame during playback), which is what was actually
  // behind the "not smooth" complaints -- not JS, which measured under 7%
  // of frame time. scoreRoutes sorts ascending, so slice(-routeLimit) below
  // still keeps the highest-scored/most-important routes. Users can still
  // drag the slider up to all 23,409 if they want the full network.
  const [routeLimit, setRouteLimit] = useState(3000)

  useEffect(() => {
    fetch("/airports/all")
      .then(res => res.json())
      .then(data => setAirports(data.airports))

    getRoutesData().then(setRoutes)
  }, [])

  // Routes/airports/labels -- no `aircraft` dependency, so this doesn't
  // rebuild every animation frame during playback, only when its own inputs
  // (routes/airports/routeLimit/theme/selectedAirport) actually change.
  // Route filtering here was the most expensive part of the old combined
  // memo: up to 23,409 routes filtered/rebuilt on every frame it depended
  // on `aircraft`, even though routes never change per-frame.
  const staticLayers = useMemo(() => {
    const scopedRoutes = selectedAirport
      ? routes.filter(r => r.Origin === selectedAirport || r.Dest === selectedAirport)
      : routes
    const visibleRoutes = routeLimit != null ? scopedRoutes.slice(-routeLimit) : scopedRoutes

    return [
      new PathLayer({
        id: "routes",
        data: visibleRoutes.filter(d => d.path && d.path.length > 0),
        getPath: d => d.path,
        getWidth: d => lerp(ROUTE_WIDTH_PX, d.score),
        getColor: d => {
          const [r, g, b] = d.avg_delay > 15 ? [255, 80, 80] : [80, 140, 255]
          return [r, g, b, lerp(ROUTE_OPACITY, d.score)]
        },
        widthUnits: "pixels",
        pickable: true,
      }),
      // Static airport dots -- no click behavior, just display.
      new ScatterplotLayer({
        id: "airports",
        data: airports,
        getPosition: d => [d.lon, d.lat],
        getRadius: d => Math.sqrt(d.total_legs) * 50 * (d.Origin === selectedAirport ? 1.8 : 1),
        getFillColor: d => {
          const base = d.inheritance_rate > 0.12 ? [255, 100, 100] : [100, 200, 255]
          if (!selectedAirport) return [...base, 255]
          return d.Origin === selectedAirport ? [255, 210, 60, 255] : [...base, 60]
        },
        radiusUnits: "meters",
        pickable: true,
        updateTriggers: {
          getFillColor: [selectedAirport],
          getRadius: [selectedAirport],
        },
      }),
      new TextLayer({
        id: "airport-labels",
        data: airports,
        getPosition: d => [d.lon, d.lat],
        getText: d => d.Origin,
        getSize: 11,
        sizeUnits: "pixels",
        getColor: hexToRgb(theme.textPrimary),
        getPixelOffset: [0, -12],
        fontFamily: "'JetBrains Mono', monospace",
        fontWeight: 600,
        background: true,
        getBackgroundColor: [...hexToRgb(theme.cardBg), 200],
        backgroundPadding: [3, 1],
      }),
    ]
  }, [routes, airports, routeLimit, theme, selectedAirport])

  // Aircraft dots -- the only layer that legitimately needs to rebuild every
  // animation frame during playback, since position is re-interpolated then.
  const aircraftLayer = useMemo(() => {
    if (!aircraft || aircraft.length === 0) return null
    const positioned = aircraft.filter(a => a.lat != null && a.lon != null)

    return new ScatterplotLayer({
      id: "aircraft",
      data: positioned,
      getPosition: d => [d.lon, d.lat],
      getRadius: 6000,
      radiusMinPixels: 6,
      radiusMaxPixels: 14,
      getFillColor: d => STATUS_COLOR[d.status] ?? [200, 200, 200],
      pickable: true,
      onClick: (info) => {
        console.log("clicked:", info.object)
        if (info.object) {
          onAircraftClick(info.object)
        }
      },
      updateTriggers: {
        // colorRevision, not `aircraft` -- see comment on the colorRevision
        // param above.
        getFillColor: [colorRevision],
      },
    })
  }, [aircraft, onAircraftClick, colorRevision])

  const layers = useMemo(
    () => (aircraftLayer ? [...staticLayers, aircraftLayer] : staticLayers),
    [staticLayers, aircraftLayer]
  )

  return (
    <div style={{ width: "100%", height, position: "relative" }}>
      <DeckGL
        initialViewState={INITIAL_VIEW}
        controller={true}
        layers={layers}
        style={{ position: "relative", width: "100%", height: "100%" }}
      >
        <Map
          mapStyle={theme.mapStyleUrl}
          attributionControl={{ compact: true }}
        />
      </DeckGL>
      {routes.length > 0 && (
        <div style={{
          position: "absolute", top: "12px", left: "12px", zIndex: 1,
          display: "flex", alignItems: "center", gap: "8px",
          background: theme.cardBg, border: `1px solid ${theme.border}`,
          borderRadius: "8px", padding: "6px 10px", minWidth: "180px",
          fontFamily: "'Inter', system-ui, sans-serif",
        }}>
          <span style={{ fontSize: "11px", fontWeight: 600, color: theme.textSecondary, whiteSpace: "nowrap" }}>
            Top {routeLimit ?? routes.length} of {routes.length}
          </span>
          <input
            type="range"
            min={Math.min(10, routes.length)}
            max={routes.length}
            step={1}
            value={routeLimit ?? routes.length}
            onChange={e => setRouteLimit(Number(e.target.value))}
            style={{ flex: 1, accentColor: theme.accentColor, height: "4px", cursor: "pointer" }}
          />
        </div>
      )}
    </div>
  )
}

// memo(): during playback `aircraft`/`colorRevision` genuinely change every
// frame so this still re-renders then, same as before -- but it stops
// wasted re-renders from unrelated Live() state changes while paused (e.g.
// typing in search, opening the tutorial modal).
export default memo(MapView)