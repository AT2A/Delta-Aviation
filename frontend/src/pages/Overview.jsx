import MapView from "../components/MapView"
import Sidebar from "../components/Sidebar"
import { useState, useEffect } from "react"

function Overview() {
  const [airports, setAirports] = useState([])

  useEffect(() => {
    fetch("/airports")
      .then(res => res.json())
      .then(data => setAirports(data.airports))
  }, [])

  return (
    <div style={{ display: "flex", width: "100vw", height: "100vh" }}>
      <Sidebar airports={airports} />
      <div style={{ flex: 1 }}>
        <MapView />
      </div>
    </div>
  )
}

export default Overview
