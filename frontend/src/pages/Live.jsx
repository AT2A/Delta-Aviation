import { useState, useEffect } from "react"
import MapView from "../components/MapView"

const DEFAULT_MINUTES = 8 * 60 // 08:00
const MAX_MINUTES = 1439
const STEP_MINUTES = 5
const TICK_MS = 500

function minutesToHHMM(minutes) {
  const h = String(Math.floor(minutes / 60)).padStart(2, "0")
  const m = String(minutes % 60).padStart(2, "0")
  return `${h}:${m}`
}

function Live() {
  const [draftMinutes, setDraftMinutes] = useState(DEFAULT_MINUTES)
  const [committedMinutes, setCommittedMinutes] = useState(DEFAULT_MINUTES)
  const [aircraftState, setAircraftState] = useState(null)
  const [playing, setPlaying] = useState(false)
  const [replayDate, setReplayDate] = useState("2025-06-12")
  const [selectedAircraft, setSelectedAircraft] = useState(null)
  const [disruptResult, setDisruptResult] = useState(null)

  useEffect(() => {
    fetch(`/state?date=${replayDate}&time=${minutesToHHMM(committedMinutes)}`)
      .then(res => res.json())
      .then(data => setAircraftState(data.aircraft))
  }, [committedMinutes, replayDate])

  useEffect(() => {
    if (!playing) return

    const interval = setInterval(() => {
      setDraftMinutes(prev => {
        const next = prev + STEP_MINUTES
        if (next > MAX_MINUTES) {
          setPlaying(false)
          return prev
        }
        setCommittedMinutes(next)
        return next
      })
    }, TICK_MS)

    return () => clearInterval(interval)
  }, [playing])

  const commit = () => setCommittedMinutes(draftMinutes)

  const handleAircraftClick = (aircraftObj) => {
    setSelectedAircraft(aircraftObj)
    setDisruptResult(null)
  }

  const closePanel = () => {
    setSelectedAircraft(null)
    setDisruptResult(null)
  }

  const handleCancel = () => {
    fetch("/disrupt", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tail_number: selectedAircraft.tail_number,
        date: replayDate,
        time: minutesToHHMM(committedMinutes),
        origin: selectedAircraft.origin,
        destination: selectedAircraft.destination,
      }),
    })
      .then(res => res.json())
      .then(data => setDisruptResult(data))
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", width: "100vw", height: "calc(100vh - 64px)" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "16px",
          padding: "16px 32px",
          background: "#ffffff",
          borderBottom: "1px solid #e2e5e9",
          fontFamily: "'Inter', system-ui, sans-serif",
        }}
      >
        <span style={{ fontSize: "13px", fontWeight: 600, color: "#5b6472" }}>
          Time
        </span>
        <input
          type="range"
          min={0}
          max={MAX_MINUTES}
          step={1}
          value={draftMinutes}
          onChange={e => setDraftMinutes(Number(e.target.value))}
          onMouseUp={commit}
          onTouchEnd={commit}
          onKeyUp={commit}
          style={{ flex: 1 }}
        />
        <button onClick={() => setPlaying(p => !p)}>
          {playing ? "Pause" : "Play"}
        </button>
        <span
          style={{
            fontSize: "13px",
            fontFamily: "'JetBrains Mono', monospace",
            color: "#14181f",
            minWidth: "48px",
          }}
        >
          {minutesToHHMM(draftMinutes)}
        </span>
        <input
          type="date"
          value={replayDate}
          onChange={e => setReplayDate(e.target.value)}
          style={{
            fontSize: "13px",
            fontFamily: "'JetBrains Mono', monospace",
            border: "1px solid #e2e5e9",
            borderRadius: "6px",
            padding: "6px 8px",
          }}
        />
      </div>

      <div style={{ display: "flex", flex: 1, position: "relative" }}>
        <div style={{ flex: 1, position: "relative" }}>
          {aircraftState === null && (
            <div
              style={{
                position: "absolute",
                inset: 0,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontFamily: "'Inter', system-ui, sans-serif",
                fontSize: "13px",
                color: "#5b6472",
                background: "#f0f1f3",
                zIndex: 1,
              }}
            >
              Loading aircraft positions…
            </div>
          )}
          <MapView
            aircraft={aircraftState ?? []}
            onAircraftClick={handleAircraftClick}
          />
        </div>

        {selectedAircraft && (
          <div
            style={{
              width: "320px",
              height: "100%",
              background: "#ffffff",
              borderLeft: "1px solid #e2e5e9",
              padding: "20px",
              overflowY: "auto",
              boxSizing: "border-box",
              fontFamily: "'Inter', system-ui, sans-serif",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontFamily: "'JetBrains Mono', monospace", fontWeight: 700, fontSize: "16px" }}>
                {selectedAircraft.tail_number}
              </span>
              <button
                onClick={closePanel}
                style={{ border: "none", background: "none", cursor: "pointer", fontSize: "18px" }}
              >
                ×
              </button>
            </div>

            <div style={{ fontSize: "14px", color: "#5b6472", marginTop: "12px" }}>
              {selectedAircraft.origin} → {selectedAircraft.destination}
            </div>
            <div style={{ fontSize: "13px", color: "#8b93a0", marginTop: "4px" }}>
              Status: {selectedAircraft.status}
            </div>

            <button onClick={handleCancel} style={{ marginTop: "16px" }}>
              Cancel this flight
            </button>

            {disruptResult && (
              <div style={{ marginTop: "16px", fontSize: "12px" }}>
                <div><strong>Cancelled:</strong> {disruptResult.cancelled_leg}</div>
                <div style={{ marginTop: "8px" }}>
                  <strong>Downstream legs ({disruptResult.downstream_legs.length}):</strong>
                  <ul>
                    {disruptResult.downstream_legs.map((leg, i) => <li key={i}>{leg}</li>)}
                  </ul>
                </div>
                <div><strong>Total cascade delay:</strong> {disruptResult.total_cascade_minutes} min</div>
                <div style={{ marginTop: "8px" }}>
                  <strong>Swap candidates:</strong>
                  <ul>
                    {disruptResult.swap_candidates.map((tail, i) => <li key={i}>{tail}</li>)}
                  </ul>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

export default Live