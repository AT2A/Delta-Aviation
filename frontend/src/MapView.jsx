import DeckGL from "@deck.gl/react"
import { Map } from "react-map-gl/maplibre"
import { ScatterplotLayer, ArcLayer } from "@deck.gl/layers"
import { useState, useEffect } from "react"
import "maplibre-gl/dist/maplibre-gl.css"

const INITIAL_VIEW = {
  longitude: -98.5,
  latitude: 39.5,
  zoom: 4,
}

function MapView() {
  const [airports, setAirports] = useState([])
  const [routes, setRoutes] = useState([])

  useEffect(() => {
    fetch("/airports/all")
    .then(res => res.json())
    .then(data => setAirports(data.airports))

    fetch("/routes")
      .then(res => res.json())
      .then(data => setRoutes(data.routes))
  }, [])

  const airportLookup = {}
  airports.forEach(a => {
    airportLookup[a.Origin] = { lat: a.lat, lon: a.lon }
  })


  const layers = [
    new ArcLayer({
      id: "routes",
      data: routes,
      getSourcePosition: d => [
        airportLookup[d.Origin]?.lon ?? 0,
        airportLookup[d.Origin]?.lat ?? 0,
      ],
      getTargetPosition: d => [
        airportLookup[d.Dest]?.lon ?? 0,
        airportLookup[d.Dest]?.lat ?? 0,
      ],
      getWidth: d => Math.sqrt(d.flight_count) * 0.1,
      getSourceColor: d => d.avg_delay > 15 ? [255, 100, 100, 120] : [80, 140, 255, 80],
      getTargetColor: d => d.avg_delay > 15 ? [255, 50, 50, 180] : [50, 100, 255, 120],
      pickable: true,
    }),
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