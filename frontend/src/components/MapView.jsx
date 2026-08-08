import DeckGL from "@deck.gl/react"
import { Map } from "react-map-gl/maplibre"
import { ScatterplotLayer, PathLayer, TextLayer } from "@deck.gl/layers"
import { useState, useEffect, useMemo } from "react"
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
function MapView({ aircraft, onAircraftClick, height = "100vh", selectedAirport = null }) {
  const { theme } = useTheme()
  const [airports, setAirports] = useState([])
  const [routes, setRoutes] = useState([])
  const [routeLimit, setRouteLimit] = useState(null) // null = no cap (show all)

  useEffect(() => {
    fetch("/airports/all")
      .then(res => res.json())
      .then(data => setAirports(data.airports))

    fetch("/routes")
      .then(res => res.json())
      .then(data => setRoutes(scoreRoutes(data.routes.map(r => ({
        ...r,
        // Flip [lat, lon] -> [lon, lat] once here instead of per-render
        // inside getPath (which deck.gl would otherwise re-run on every
        // route on every re-render, including ones where routes hasn't
        // actually changed).
        path: r.path ? r.path.map(([lat, lon]) => [lon, lat]) : r.path,
      })))))
  }, [])

  const layers = useMemo(() => {
    const scopedRoutes = selectedAirport
      ? routes.filter(r => r.Origin === selectedAirport || r.Dest === selectedAirport)
      : routes
    const visibleRoutes = routeLimit != null ? scopedRoutes.slice(-routeLimit) : scopedRoutes

    const result = [
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

    if (aircraft && aircraft.length > 0) {
      const positioned = aircraft.filter(a => a.lat != null && a.lon != null)

      result.push(
        new ScatterplotLayer({
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
            getFillColor: [aircraft],
          },
        })
      )
    }

    return result
  }, [routes, airports, aircraft, onAircraftClick, routeLimit, theme, selectedAirport])

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

export default MapView