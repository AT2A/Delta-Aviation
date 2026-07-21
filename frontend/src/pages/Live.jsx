import { useState, useEffect, useRef, useCallback } from "react"
import MapView from "../components/MapView"

const DEFAULT_MINUTES = 8 * 60 // 08:00
const MAX_MINUTES = 1439
const STEP_MINUTES = 5
const TICK_MS = 500

const RANKING_MODES = [
  { value: "minimize_this_flight_delay", label: "Minimize this flight's delay" },
  { value: "minimize_total_delay", label: "Minimize total propagated delay" },
  { value: "protect_other_flights", label: "Protect other flights" },
  { value: "protect_major_flights", label: "Protect major flights" },
  { value: "all_factors_combined", label: "All factors combined" },
]

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
  const [selectedMode, setSelectedMode] = useState("all_factors_combined")
  const [aircraftDownResult, setAircraftDownResult] = useState(null)
  const [solver, setSolver] = useState("greedy")
  const [isGrounding, setIsGrounding] = useState(false)
  const [isDisrupting, setIsDisrupting] = useState(false)
  const stateRequestId = useRef(0)

  useEffect(() => {
    const requestId = ++stateRequestId.current
    fetch(`/state?date=${replayDate}&time=${minutesToHHMM(committedMinutes)}`)
      .then(res => res.json())
      .then(data => {
        // Ignore responses to a request that's no longer the latest one --
        // during playback these fire every 500ms and can resolve out of
        // order, which would otherwise visibly jump the map backward.
        if (requestId === stateRequestId.current) {
          setAircraftState(data.aircraft)
        }
      })
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

  // Stable reference (useState setters are guaranteed stable) so MapView's
  // layers useMemo isn't invalidated by a fresh function on every render.
  const handleAircraftClick = useCallback((aircraftObj) => {
    setSelectedAircraft(aircraftObj)
    setDisruptResult(null)
    setAircraftDownResult(null)
  }, [])

  const closePanel = () => {
    setSelectedAircraft(null)
    setDisruptResult(null)
    setAircraftDownResult(null)
  }

  const handleCancel = () => {
    if (isDisrupting) return
    setIsDisrupting(true)
    fetch("/disrupt", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tail_number: selectedAircraft.tail_number,
        date: replayDate,
        time: minutesToHHMM(committedMinutes),
        origin: selectedAircraft.origin,
        destination: selectedAircraft.destination,
        mode: selectedMode,
      }),
    })
      .then(res => res.json())
      .then(data => setDisruptResult(data))
      .finally(() => setIsDisrupting(false))
  }

  // Changing mode after a result already exists re-runs the same disruption
  // under the new weighting -- simplest is to just re-fetch automatically,
  // consistent with how the rest of this file re-fetches on state change.
  const handleModeChange = (e) => {
    if (isDisrupting) return
    const newMode = e.target.value
    setSelectedMode(newMode)
    if (disruptResult) {
      setIsDisrupting(true)
      fetch("/disrupt", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tail_number: selectedAircraft.tail_number,
          date: replayDate,
          time: minutesToHHMM(committedMinutes),
          origin: selectedAircraft.origin,
          destination: selectedAircraft.destination,
          mode: newMode,
        }),
      })
        .then(res => res.json())
        .then(data => setDisruptResult(data))
        .finally(() => setIsDisrupting(false))
    }
  }

  const handleGroundAircraft = () => {
    if (isGrounding) return
    setAircraftDownResult(null)
    setIsGrounding(true)
    fetch("/disrupt/aircraft-down", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tail_number: selectedAircraft.tail_number,
        date: replayDate,
        time: minutesToHHMM(committedMinutes),
        mode: selectedMode,
        solver: solver,
      }),
    })
      .then(res => res.json())
      .then(data => setAircraftDownResult(data))
      .finally(() => setIsGrounding(false))
  }

  const formatLegs = (legs) => legs.map(([u, v]) => `${u}→${v}`).join(", ")

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
              width: "340px",
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

            <div style={{ marginTop: "16px" }}>
              <label style={{ fontSize: "11px", fontWeight: 700, color: "#5b6472", textTransform: "uppercase", letterSpacing: "0.03em" }}>
                Ranking mode
              </label>
              <select
                value={selectedMode}
                onChange={handleModeChange}
                disabled={isDisrupting}
                style={{
                  width: "100%",
                  marginTop: "6px",
                  padding: "8px",
                  fontSize: "13px",
                  border: "1px solid #e2e5e9",
                  borderRadius: "6px",
                  fontFamily: "'Inter', system-ui, sans-serif",
                }}
              >
                {RANKING_MODES.map(m => (
                  <option key={m.value} value={m.value}>{m.label}</option>
                ))}
              </select>
            </div>

            <button
              onClick={handleCancel}
              disabled={isDisrupting}
              style={{
                marginTop: "12px",
                width: "100%",
                padding: "10px",
                opacity: isDisrupting ? 0.6 : 1,
                cursor: isDisrupting ? "default" : "pointer",
              }}
            >
              {isDisrupting ? "Working..." : "Cancel this flight"}
            </button>

            <div style={{ marginTop: "20px" }}>
              <label style={{ fontSize: "11px", fontWeight: 700, color: "#5b6472", textTransform: "uppercase", letterSpacing: "0.03em" }}>
                Solver
              </label>
              <select
                value={solver}
                onChange={e => setSolver(e.target.value)}
                disabled={isGrounding}
                style={{
                  width: "100%",
                  marginTop: "6px",
                  padding: "8px",
                  fontSize: "13px",
                  border: "1px solid #e2e5e9",
                  borderRadius: "6px",
                  fontFamily: "'Inter', system-ui, sans-serif",
                }}
              >
                <option value="greedy">Greedy (fast)</option>
                <option value="optimal">Optimal (slower, best answer)</option>
              </select>
            </div>

            <button
              onClick={handleGroundAircraft}
              disabled={isGrounding}
              style={{
                marginTop: "12px",
                width: "100%",
                padding: "10px",
                opacity: isGrounding ? 0.6 : 1,
                cursor: isGrounding ? "default" : "pointer",
              }}
            >
              {isGrounding ? "Grounding..." : "Ground this aircraft"}
            </button>

            {aircraftDownResult && (
              <div style={{ marginTop: "20px" }}>
                <div
                  style={{
                    background: "#eaf6ef",
                    border: "1px solid #cdeaD9",
                    borderRadius: "8px",
                    padding: "14px",
                  }}
                >
                  <div style={{ fontSize: "11px", fontWeight: 700, color: "#2f8f5b", textTransform: "uppercase", letterSpacing: "0.03em" }}>
                    Recovery coverage · {aircraftDownResult.solver} solver
                  </div>
                  <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "26px", fontWeight: 800, color: "#2f8f5b", marginTop: "4px" }}>
                    {aircraftDownResult.total_covered_legs} / {aircraftDownResult.total_orphaned_legs} legs covered
                  </div>
                  <div style={{ fontSize: "11px", color: "#5b6472", marginTop: "6px" }}>
                    {aircraftDownResult.total_uncovered_legs} leg(s) uncovered · {aircraftDownResult.num_segments} segment(s) ·{" "}
                    {aircraftDownResult.total_induced_delay_minutes.toFixed(0)} min total induced delay
                  </div>
                </div>

                <h3 style={{ fontSize: "12px", fontWeight: 700, color: "#14181f", textTransform: "uppercase", letterSpacing: "0.03em", marginTop: "18px", marginBottom: "8px" }}>
                  Recovery segments
                </h3>

                {aircraftDownResult.segments.map((seg, i) => (
                  seg.tail_number ? (
                    <div
                      key={i}
                      style={{
                        border: "1px solid #e2e5e9",
                        borderRadius: "8px",
                        padding: "10px 12px",
                        marginBottom: "8px",
                        background: "#ffffff",
                      }}
                    >
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                        <span style={{ fontFamily: "'JetBrains Mono', monospace", fontWeight: 700, fontSize: "13px" }}>
                          {seg.tail_number}
                        </span>
                      </div>
                      <div style={{ fontSize: "11px", color: "#5b6472", marginTop: "4px" }}>
                        {formatLegs(seg.legs_covered)}
                      </div>
                      <div style={{ fontSize: "11px", color: "#5b6472", marginTop: "4px" }}>
                        {seg.induced_delay_minutes != null ? `Delay: ${seg.induced_delay_minutes.toFixed(0)} min · ` : ""}
                        {seg.score != null ? `Score: ${seg.score.toFixed(2)}` : ""}
                      </div>
                    </div>
                  ) : (
                    <div
                      key={i}
                      style={{
                        border: "1px solid #f3b8b0",
                        borderRadius: "8px",
                        padding: "10px 12px",
                        marginBottom: "8px",
                        background: "#fdecea",
                      }}
                    >
                      <div style={{ fontSize: "13px", fontWeight: 700, color: "#c0392b" }}>
                        No substitute found for {formatLegs(seg.legs_covered)}
                      </div>
                    </div>
                  )
                ))}
              </div>
            )}

            {disruptResult && (
              <div style={{ marginTop: "20px" }}>
                {/* Headline improvement number, with honest supporting detail underneath -- never collapsed into just the percentage alone. */}
                <div
                  style={{
                    background: "#eaf6ef",
                    border: "1px solid #cdeaD9",
                    borderRadius: "8px",
                    padding: "14px",
                  }}
                >
                  <div style={{ fontSize: "11px", fontWeight: 700, color: "#2f8f5b", textTransform: "uppercase", letterSpacing: "0.03em" }}>
                    Network disruption avoided
                  </div>
                  <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "26px", fontWeight: 800, color: "#2f8f5b", marginTop: "4px" }}>
                    {disruptResult.improvement_pct !== null ? `${disruptResult.improvement_pct.toFixed(1)}%` : "—"}
                  </div>
                  <div style={{ fontSize: "11px", color: "#5b6472", marginTop: "6px" }}>
                    {disruptResult.cancellations_avoided} leg(s) kept flying instead of cancelled ·{" "}
                    {disruptResult.total_induced_delay_minutes !== null
                      ? `${disruptResult.total_induced_delay_minutes.toFixed(0)} min induced delay`
                      : "no substitute available"}
                  </div>
                  <div style={{ fontSize: "10px", color: "#9aa1ac", marginTop: "6px" }}>
                    Headline % assumes a 300 min modeling estimate per avoided cancellation — a stated assumption, not derived from data.
                  </div>
                </div>

                <h3 style={{ fontSize: "12px", fontWeight: 700, color: "#14181f", textTransform: "uppercase", letterSpacing: "0.03em", marginTop: "18px", marginBottom: "8px" }}>
                  Ranked candidates
                </h3>

                {disruptResult.ranked_candidates.length === 0 && (
                  <div style={{ fontSize: "13px", color: "#8b93a0" }}>
                    No viable substitute found — these legs would be cancelled.
                  </div>
                )}

                {disruptResult.ranked_candidates.map((c, i) => (
                  <div
                    key={c.tail_number}
                    style={{
                      border: i === 0 ? "2px solid #2f5fd6" : "1px solid #e2e5e9",
                      borderRadius: "8px",
                      padding: "10px 12px",
                      marginBottom: "8px",
                      background: i === 0 ? "#eaefff" : "#ffffff",
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <span style={{ fontFamily: "'JetBrains Mono', monospace", fontWeight: 700, fontSize: "13px" }}>
                        {c.tail_number}
                      </span>
                      {i === 0 && (
                        <span style={{ fontSize: "10px", fontWeight: 700, color: "#2f5fd6", textTransform: "uppercase" }}>
                          Recommended
                        </span>
                      )}
                    </div>
                    <div style={{ fontSize: "11px", color: "#5b6472", marginTop: "4px" }}>
                      Delay: {c.delay.toFixed(0)} min · Network disruption: {c.network_disruption.toFixed(0)} min · Connections at risk: {c.connecting_flights_missed}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

export default Live