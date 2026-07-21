import DeckGL from "@deck.gl/react"
import { Map } from "react-map-gl/maplibre"
import { ScatterplotLayer, PathLayer } from "@deck.gl/layers"
import { useState, useEffect, useMemo } from "react"
import "maplibre-gl/dist/maplibre-gl.css"

const INITIAL_VIEW = {
  longitude: -98.5,
  latitude: 39.5,
  zoom: 4,
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
function MapView({ aircraft, onAircraftClick }) {
  const [airports, setAirports] = useState([])
  const [routes, setRoutes] = useState([])

  useEffect(() => {
    fetch("/airports/all")
      .then(res => res.json())
      .then(data => setAirports(data.airports))

    fetch("/routes")
      .then(res => res.json())
      .then(data => setRoutes(data.routes.map(r => ({
        ...r,
        // Flip [lat, lon] -> [lon, lat] once here instead of per-render
        // inside getPath (which deck.gl would otherwise re-run on every
        // route on every re-render, including ones where routes hasn't
        // actually changed).
        path: r.path ? r.path.map(([lat, lon]) => [lon, lat]) : r.path,
      }))))
  }, [])

  const layers = useMemo(() => {
    const result = [
      new PathLayer({
        id: "routes",
        data: routes.filter(d => d.path && d.path.length > 0),
        getPath: d => d.path,
        getWidth: d => Math.sqrt(d.flight_count) * 0.1,
        getColor: d => d.avg_delay > 15 ? [255, 80, 80, 160] : [80, 140, 255, 120],
        widthUnits: "pixels",
        pickable: true,
      }),
      // Static airport dots -- no click behavior, just display.
      new ScatterplotLayer({
        id: "airports",
        data: airports,
        getPosition: d => [d.lon, d.lat],
        getRadius: d => Math.sqrt(d.total_legs) * 50,
        getFillColor: d => d.inheritance_rate > 0.12
          ? [255, 100, 100]
          : [100, 200, 255],
        radiusUnits: "meters",
        pickable: true,
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
  }, [routes, airports, aircraft, onAircraftClick])

  return (
    <div style={{ width: "100%", height: "100vh" }}>
      <DeckGL
        initialViewState={INITIAL_VIEW}
        controller={true}
        layers={layers}
        style={{ position: "relative", width: "100%", height: "100%" }}
      >
        <Map
          mapStyle="https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json"
        />
      </DeckGL>
    </div>
  )
}

export default MapView