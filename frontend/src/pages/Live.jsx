import { useState, useEffect, useRef, useCallback, useMemo } from "react"
import MapView from "../components/MapView"
import AirportAutocomplete from "../components/AirportAutocomplete"
import { useTheme } from "../ThemeContext"

const DEFAULT_MINUTES = 8 * 60 // 08:00
const MAX_MINUTES = 1439
// Playback speed: simulated minutes advanced per real millisecond while
// playing (unchanged from the original 5-min-per-500ms rate) -- now applied
// continuously via requestAnimationFrame instead of a fixed-step interval,
// so dot movement is smooth regardless of how often we poll the backend.
const STEP_MINUTES = 5
const TICK_MS = 500
const PLAYBACK_RATE_MIN_PER_MS = STEP_MINUTES / TICK_MS
// How often we ask the backend for a fresh snapshot while playing. Kept
// well above typical /state response time; combined with fetchInFlightRef
// below, we never have more than one /state request in flight at once.
const STATE_REFRESH_MS = 1000

// Mirrors backend/analysis/geo.py's great_circle_interpolate exactly, so
// the frontend can smoothly re-derive an in_flight aircraft's position for
// any moment between /state polls instead of only at fetched keyframes.
function greatCircleInterpolate(lat1, lon1, lat2, lon2, frac) {
  const toRad = d => (d * Math.PI) / 180
  const toDeg = r => (r * 180) / Math.PI
  const rlat1 = toRad(lat1), rlon1 = toRad(lon1), rlat2 = toRad(lat2), rlon2 = toRad(lon2)
  const d = Math.acos(Math.min(1, Math.max(-1,
    Math.sin(rlat1) * Math.sin(rlat2) + Math.cos(rlat1) * Math.cos(rlat2) * Math.cos(rlon2 - rlon1)
  )))
  if (d === 0) return [lat1, lon1]
  const a = Math.sin((1 - frac) * d) / Math.sin(d)
  const b = Math.sin(frac * d) / Math.sin(d)
  const x = a * Math.cos(rlat1) * Math.cos(rlon1) + b * Math.cos(rlat2) * Math.cos(rlon2)
  const y = a * Math.cos(rlat1) * Math.sin(rlon1) + b * Math.cos(rlat2) * Math.sin(rlon2)
  const z = a * Math.sin(rlat1) + b * Math.sin(rlat2)
  return [toDeg(Math.atan2(z, Math.sqrt(x * x + y * y))), toDeg(Math.atan2(y, x))]
}

// Local midnight of `dateStr` plus `minutes` (fractional minutes allowed),
// matching how wheels_off/wheels_on ISO strings are parsed by `new Date`.
function minutesToDate(dateStr, minutes) {
  const base = new Date(`${dateStr}T00:00:00`)
  return new Date(base.getTime() + minutes * 60000)
}

function hhmmToMinutes(hhmm) {
  const [h, m] = hhmm.split(":").map(Number)
  return h * 60 + m
}

// Curated flights where Feature 1/2 produce clear, illustrative results --
// found by sampling real /disrupt and /disrupt/aircraft-down output across
// many tail/date combinations, not fabricated. Jumping to one of these
// loads the real date/time and selects the real aircraft; every number
// shown afterward still comes from a live backend call.
const DEMO_EXAMPLES = [
  {
    tail_number: "N372DN",
    date: "2025-07-30",
    time: "11:30",
    // The leg being evaluated for cancellation -- not necessarily where the
    // aircraft physically is at `time` (it's mid-rotation on an earlier leg
    // then), same as how a real dispatcher would decide "as of now, what if
    // we cancelled this upcoming leg". Overrides the /state snapshot's
    // origin/destination once selected -- see the pendingDemoExample effect.
    origin: "ATL",
    destination: "LGA",
    feature: "cancel",
    label: "N372DN · ATL→LGA",
    blurb: "94.7% improvement, 3 legs saved. Switch to \"Protect major flights\" and watch the Recommended pick change.",
  },
  {
    tail_number: "N935AT",
    date: "2025-05-01",
    time: "07:10",
    origin: "HSV",
    destination: "ATL",
    feature: "cancel",
    label: "N935AT · HSV→ATL",
    blurb: "98.4% improvement, 5 legs saved. The top candidate's disruption score is a fraction of the runner-ups'.",
  },
  {
    tail_number: "N992AT",
    date: "2025-01-01",
    time: "08:51",
    feature: "ground",
    label: "N992AT",
    blurb: "4 orphaned legs — Greedy covers 3, Optimal covers all 4. Try switching solvers.",
  },
  {
    tail_number: "N899AT",
    date: "2025-01-01",
    time: "10:18",
    feature: "ground",
    label: "N899AT",
    blurb: "3 orphaned legs — Greedy covers 2, Optimal covers all 3.",
  },
  {
    tail_number: "N928AT",
    date: "2025-01-01",
    time: "07:14",
    feature: "ground",
    label: "N928AT",
    blurb: "5 orphaned legs, the biggest gap found — Greedy covers 3, Optimal recovers all 5.",
  },
]

// Aircraft not currently in flight -- i.e. plausible to ground right now.
const ELIGIBLE_STATUSES = ["taxiing_out", "taxiing_in", "parked", "not_yet_started"]

const RANKING_MODES = [
  { value: "minimize_this_flight_delay", label: "Minimize this flight's delay" },
  { value: "minimize_total_delay", label: "Minimize total propagated delay" },
  { value: "protect_other_flights", label: "Protect other flights" },
  { value: "protect_major_flights", label: "Protect major flights" },
  { value: "all_factors_combined", label: "All factors combined" },
]

// Mirrors MapView.jsx's internal STATUS_COLOR map (MapView.jsx:14-22).
// Keep these two lists in sync manually if aircraft statuses/colors change.
// taxiing_out and taxiing_in share a color in MapView.jsx, so they are
// collapsed into one "Taxiing" legend row here.
const STATUS_LEGEND = [
  { label: "In flight", color: "rgb(80, 200, 120)" },
  { label: "Taxiing", color: "rgb(230, 190, 60)" },
  { label: "Parked", color: "rgb(140, 150, 160)" },
  { label: "Not yet started", color: "rgb(90, 110, 200)" },
  { label: "Cancelled", color: "rgb(220, 60, 60)" },
  { label: "Not operating", color: "rgb(70, 75, 85)" },
]

const TUTORIAL_SECTIONS = [
  {
    title: "Time & playback",
    body: "Drag the time slider or press Play to step through the day in 5-minute increments. Use the date picker to replay a different day.",
  },
  {
    title: "Select an aircraft",
    body: "Click any aircraft dot on the map to open its detail panel on the right. Dot color shows status — see the legend in the bottom-left of the map.",
  },
  {
    title: "Cancel this flight",
    body: "Pick a ranking mode, then click \"Cancel this flight\" to see which other aircraft could realistically cover the leg, ranked by that mode, plus the modeled improvement in network disruption.",
  },
  {
    title: "Ground this aircraft",
    body: "Pick a solver — Greedy is fast, Optimal searches harder for a better answer with a timeout — then click \"Ground this aircraft\" to see how many of that tail's remaining legs the network can recover with substitute aircraft, and which (if any) are left uncovered.",
  },
]

function minutesToHHMM(minutes) {
  const h = String(Math.floor(minutes / 60)).padStart(2, "0")
  const m = String(minutes % 60).padStart(2, "0")
  return `${h}:${m}`
}

function Live() {
  const { theme } = useTheme()
  const [draftMinutes, setDraftMinutes] = useState(DEFAULT_MINUTES)
  const [committedMinutes, setCommittedMinutes] = useState(DEFAULT_MINUTES)
  // Continuously-advancing clock used only while playing, driven by
  // requestAnimationFrame -- this (not committedMinutes) is what makes
  // aircraft positions and the time readout move smoothly instead of
  // jumping once per backend poll.
  const [displayMinutes, setDisplayMinutes] = useState(DEFAULT_MINUTES)
  const [aircraftState, setAircraftState] = useState(null)
  const [playing, setPlaying] = useState(false)
  const [replayDate, setReplayDate] = useState("2025-06-12")
  const [selectedAircraft, setSelectedAircraft] = useState(null)
  const [disruptResult, setDisruptResult] = useState(null)
  const [disruptError, setDisruptError] = useState(null)
  const [selectedMode, setSelectedMode] = useState("all_factors_combined")
  const [aircraftDownResult, setAircraftDownResult] = useState(null)
  const [solver, setSolver] = useState("greedy")
  const [isGrounding, setIsGrounding] = useState(false)
  const [isDisrupting, setIsDisrupting] = useState(false)
  const [demoMode, setDemoMode] = useState(false)
  const [tutorialOpen, setTutorialOpen] = useState(false)
  // The DEMO_EXAMPLES entry we're waiting on a fresh /state snapshot for.
  const [pendingDemoExample, setPendingDemoExample] = useState(null)
  const stateRequestId = useRef(0)
  const fetchInFlightRef = useRef(false)
  const [airportOptions, setAirportOptions] = useState([])
  const [searchOrigin, setSearchOrigin] = useState("")
  const [searchDest, setSearchDest] = useState("")
  const [eligibleOnly, setEligibleOnly] = useState(false)

  useEffect(() => {
    fetch("/airports")
      .then(res => res.json())
      .then(data => setAirportOptions(data.airports))
  }, [])

  // Once the /state fetch triggered by goToDemoExample resolves, select the
  // real aircraft object from that snapshot -- same as clicking it on the
  // map -- but keep the example's specific origin/destination (the leg
  // being evaluated), since that can differ from wherever the aircraft
  // physically is at that exact query time.
  useEffect(() => {
    if (!pendingDemoExample || !aircraftState) return
    const match = aircraftState.find(a => a.tail_number === pendingDemoExample.tail_number)
    if (match) {
      setSelectedAircraft(
        pendingDemoExample.origin
          ? { ...match, origin: pendingDemoExample.origin, destination: pendingDemoExample.destination }
          : match
      )
      setPendingDemoExample(null)
    }
  }, [aircraftState, pendingDemoExample])

  useEffect(() => {
    const requestId = ++stateRequestId.current
    fetchInFlightRef.current = true
    fetch(`/state?date=${replayDate}&time=${minutesToHHMM(committedMinutes)}`)
      .then(res => res.json())
      .then(data => {
        // Ignore responses to a request that's no longer the latest one --
        // ordering can still occasionally shuffle even at the reduced
        // polling rate below.
        if (requestId === stateRequestId.current) {
          setAircraftState(data.aircraft)
        }
      })
      .finally(() => { fetchInFlightRef.current = false })
  }, [committedMinutes, replayDate])

  // Smooth clock: advances displayMinutes every animation frame based on
  // real elapsed time, independent of network/backend speed.
  useEffect(() => {
    if (!playing) return

    let raf
    let lastTime = null

    const frame = (now) => {
      if (lastTime == null) lastTime = now
      const dt = now - lastTime
      lastTime = now

      setDisplayMinutes(prev => {
        const next = prev + dt * PLAYBACK_RATE_MIN_PER_MS
        if (next >= MAX_MINUTES) {
          setPlaying(false)
          return MAX_MINUTES
        }
        return next
      })

      raf = requestAnimationFrame(frame)
    }

    raf = requestAnimationFrame(frame)
    return () => cancelAnimationFrame(raf)
  }, [playing])

  // Periodically refresh the backend snapshot while playing, skipping a
  // tick entirely if the previous /state request hasn't resolved yet --
  // this is what prevents requests from backing up and causing the
  // "nothing moves, then jumps" stall.
  useEffect(() => {
    if (!playing) return

    const interval = setInterval(() => {
      if (fetchInFlightRef.current) return
      setDisplayMinutes(current => {
        setCommittedMinutes(Math.min(Math.round(current), MAX_MINUTES))
        return current
      })
    }, STATE_REFRESH_MS)

    return () => clearInterval(interval)
  }, [playing])

  // While paused/scrubbing, displayMinutes should exactly mirror the
  // committed (fetched) minute -- no interpolation drift when not playing.
  useEffect(() => {
    if (!playing) setDisplayMinutes(committedMinutes)
  }, [committedMinutes, playing])

  const commit = () => setCommittedMinutes(draftMinutes)

  const togglePlaying = () => {
    setPlaying(p => {
      if (p) {
        // Pausing -- snap the slider/committed time to wherever the smooth
        // clock actually was, so scrubbing resumes from the right spot.
        const snapped = Math.round(displayMinutes)
        setDraftMinutes(snapped)
        setCommittedMinutes(snapped)
      }
      return !p
    })
  }

  // Stable reference (useState setters are guaranteed stable) so MapView's
  // layers useMemo isn't invalidated by a fresh function on every render.
  const handleAircraftClick = useCallback((aircraftObj) => {
    setSelectedAircraft(aircraftObj)
    setDisruptResult(null)
    setDisruptError(null)
    setAircraftDownResult(null)
  }, [])

  const closePanel = () => {
    setSelectedAircraft(null)
    setDisruptResult(null)
    setDisruptError(null)
    setAircraftDownResult(null)
  }

  // Aircraft that haven't started their first leg yet have no destination
  // (see /state -- only `origin` is populated), so there's nothing to cancel.
  const canCancel = selectedAircraft?.destination != null

  const handleCancel = () => {
    if (isDisrupting || !canCancel) return
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
      .then(res => res.json().then(data => ({ ok: res.ok, data })))
      .then(({ ok, data }) => {
        if (ok) {
          setDisruptResult(data)
          setDisruptError(null)
        } else {
          setDisruptResult(null)
          setDisruptError(data?.detail ?? "Failed to cancel this flight.")
        }
      })
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
        .then(res => res.json().then(data => ({ ok: res.ok, data })))
        .then(({ ok, data }) => {
          if (ok) {
            setDisruptResult(data)
            setDisruptError(null)
          } else {
            setDisruptResult(null)
            setDisruptError(data?.detail ?? "Failed to cancel this flight.")
          }
        })
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

  const toggleDemoMode = () => setDemoMode(d => !d)
  const openTutorial = () => setTutorialOpen(true)
  const closeTutorial = () => setTutorialOpen(false)

  const goToDemoExample = (example) => {
    setPlaying(false)
    setSelectedAircraft(null)
    setDisruptResult(null)
    setDisruptError(null)
    setAircraftDownResult(null)
    setSelectedMode("all_factors_combined")
    setSolver("greedy")
    const minutes = hhmmToMinutes(example.time)
    setDraftMinutes(minutes)
    setDisplayMinutes(minutes)
    setPendingDemoExample(example)
    setReplayDate(example.date)
    setCommittedMinutes(minutes)
  }

  // What's actually shown: driven by the smooth clock while playing, by the
  // slider's raw drag position otherwise.
  const visibleMinutes = playing ? displayMinutes : draftMinutes

  // Re-derive in_flight aircraft positions for the exact current instant
  // using the same great-circle math the backend used to produce
  // wheels_off/wheels_on/origin/dest coords -- everything else (parked,
  // taxiing, etc.) just keeps its last-fetched static position.
  const renderedAircraft = useMemo(() => {
    if (!aircraftState) return null
    const queryDate = minutesToDate(replayDate, visibleMinutes)
    return aircraftState.map(a => {
      if (a.status !== "in_flight" || !a.wheels_off || !a.wheels_on) return a
      const wheelsOff = new Date(a.wheels_off)
      const wheelsOn = new Date(a.wheels_on)
      const duration = wheelsOn - wheelsOff
      if (!(duration > 0)) return a
      const frac = Math.max(0, Math.min(1, (queryDate - wheelsOff) / duration))
      const [lat, lon] = greatCircleInterpolate(a.origin_lat, a.origin_lon, a.dest_lat, a.dest_lon, frac)
      return { ...a, lat, lon }
    })
  }, [aircraftState, replayDate, visibleMinutes])

  const searchActive = Boolean(searchOrigin || searchDest || eligibleOnly)
  const searchResults = useMemo(() => {
    if (!searchActive || !renderedAircraft) return []
    return renderedAircraft.filter(a =>
      (!searchOrigin || a.origin === searchOrigin) &&
      (!searchDest || a.destination === searchDest) &&
      (!eligibleOnly || ELIGIBLE_STATUSES.includes(a.status))
    )
  }, [renderedAircraft, searchOrigin, searchDest, eligibleOnly, searchActive])

  return (
    <div style={{ display: "flex", flexDirection: "column", width: "100vw", height: "calc(100vh - 64px)" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "16px",
          padding: "16px 32px",
          background: theme.cardBg,
          borderBottom: `1px solid ${theme.border}`,
          fontFamily: "'Inter', system-ui, sans-serif",
        }}
      >
        <span style={{ fontSize: "13px", fontWeight: 600, color: theme.textSecondary }}>
          Time
        </span>
        <input
          type="range"
          min={0}
          max={MAX_MINUTES}
          step={1}
          value={Math.round(visibleMinutes)}
          onChange={e => setDraftMinutes(Number(e.target.value))}
          onMouseUp={commit}
          onTouchEnd={commit}
          onKeyUp={commit}
          style={{ flex: 1, accentColor: theme.accentColor, height: "4px", cursor: "pointer" }}
        />

        <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
          <button
            onClick={togglePlaying}
            style={{
              border: `1px solid ${theme.border}`,
              background: theme.cardBg,
              borderRadius: "20px",
              padding: "7px 16px",
              fontSize: "12px",
              fontWeight: 600,
              fontFamily: "'Inter', system-ui, sans-serif",
              color: theme.textPrimary,
              cursor: "pointer",
            }}
          >
            {playing ? "Pause" : "Play"}
          </button>
          <span
            style={{
              fontSize: "16px",
              fontWeight: 700,
              fontFamily: "'JetBrains Mono', monospace",
              color: theme.textPrimary,
              minWidth: "52px",
            }}
          >
            {minutesToHHMM(Math.floor(visibleMinutes))}
          </span>
          <input
            type="date"
            value={replayDate}
            onChange={e => setReplayDate(e.target.value)}
            style={{
              fontSize: "12px",
              fontFamily: "'JetBrains Mono', monospace",
              border: `1px solid ${theme.border}`,
              borderRadius: "8px",
              padding: "6px 12px",
              color: theme.textPrimary,
              background: theme.cardBg,
            }}
          />
          <button
            onClick={toggleDemoMode}
            style={{
              border: demoMode ? `1px solid ${theme.successBorder}` : `1px solid ${theme.border}`,
              background: demoMode ? theme.successBg : theme.cardBg,
              color: demoMode ? theme.successNumber : theme.textSecondary,
              borderRadius: "20px",
              padding: "7px 16px",
              fontSize: "12px",
              fontWeight: 600,
              fontFamily: "'Inter', system-ui, sans-serif",
              cursor: "pointer",
            }}
          >
            {demoMode ? "● Demo Mode" : "Demo Mode"}
          </button>
          <button
            onClick={openTutorial}
            style={{
              border: `1px solid ${theme.border}`,
              background: theme.cardBg,
              color: theme.textSecondary,
              borderRadius: "20px",
              padding: "7px 16px",
              fontSize: "12px",
              fontWeight: 600,
              fontFamily: "'Inter', system-ui, sans-serif",
              cursor: "pointer",
            }}
          >
            Tutorial
          </button>
        </div>
      </div>

      {tutorialOpen && (
        <div
          onClick={closeTutorial}
          style={{
            position: "fixed",
            inset: 0,
            background: theme.modalOverlay,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 100,
          }}
        >
          <div
            onClick={e => e.stopPropagation()}
            style={{
              background: theme.cardBg,
              borderRadius: "4px",
              width: "480px",
              maxWidth: "90vw",
              padding: "28px 30px",
              boxShadow: "0 12px 40px rgba(0, 0, 0, 0.2)",
              fontFamily: "'Inter', system-ui, sans-serif",
            }}
          >
            <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: "16px" }}>
              <span style={{ fontSize: "16px", fontWeight: 700, color: theme.textPrimary }}>
                How to use Live Replay & Disruption
              </span>
              <button
                onClick={closeTutorial}
                style={{ border: "none", background: "none", cursor: "pointer", fontSize: "18px", lineHeight: 1, color: theme.textMuted }}
              >
                ×
              </button>
            </div>
            {TUTORIAL_SECTIONS.map(section => (
              <div key={section.title} style={{ marginBottom: "14px" }}>
                <div style={{ fontSize: "12.5px", fontWeight: 700, color: theme.textPrimary, marginBottom: "3px" }}>
                  {section.title}
                </div>
                <div style={{ fontSize: "12.5px", color: theme.textSecondary, lineHeight: 1.5 }}>
                  {section.body}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div style={{ display: "flex", flex: 1, minHeight: 0, position: "relative" }}>
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
                color: theme.textMuted,
                background: theme.pageBg,
                zIndex: 2,
              }}
            >
              Loading aircraft positions…
            </div>
          )}
          <MapView
            aircraft={renderedAircraft ?? []}
            onAircraftClick={handleAircraftClick}
            height="100%"
          />

          <div
            style={{
              position: "absolute",
              left: "16px",
              bottom: "16px",
              zIndex: 1,
              display: "flex",
              flexWrap: "wrap",
              gap: "4px 14px",
              maxWidth: "260px",
              background: "rgba(20, 24, 31, 0.82)",
              borderRadius: "20px",
              padding: "10px 16px",
              fontFamily: "'Inter', system-ui, sans-serif",
            }}
          >
            {STATUS_LEGEND.map(item => (
              <div key={item.label} style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                <span style={{ width: "8px", height: "8px", borderRadius: "50%", background: item.color, flexShrink: 0 }} />
                <span style={{ fontSize: "10.5px", color: "#e6e8ec" }}>{item.label}</span>
              </div>
            ))}
          </div>
        </div>

        <div
          style={{
            width: "360px",
            flexShrink: 0,
            height: "100%",
            background: theme.cardBg,
            borderLeft: `1px solid ${theme.border}`,
            padding: "20px",
            overflowY: "auto",
            boxSizing: "border-box",
            fontFamily: "'Inter', system-ui, sans-serif",
            color: theme.textPrimary,
          }}
        >
          {selectedAircraft ? (
            <>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                <span style={{ fontFamily: "'JetBrains Mono', monospace", fontWeight: 700, fontSize: "20px", color: theme.textPrimary }}>
                  {selectedAircraft.tail_number}
                </span>
                <button
                  onClick={closePanel}
                  style={{ border: "none", background: "none", cursor: "pointer", fontSize: "18px", lineHeight: 1, color: theme.textMuted, padding: "2px 4px" }}
                >
                  ×
                </button>
              </div>

              <div style={{ fontSize: "13px", color: theme.textSecondary, marginTop: "6px" }}>
                {selectedAircraft.origin} → {selectedAircraft.destination} · {selectedAircraft.status}
              </div>

              <div style={{ marginTop: "16px" }}>
                <label style={{ fontSize: "11px", fontWeight: 700, color: theme.textSecondary, textTransform: "uppercase", letterSpacing: "0.04em" }}>
                  Ranking mode
                </label>
                <select
                  value={selectedMode}
                  onChange={handleModeChange}
                  disabled={isDisrupting}
                  style={{
                    width: "100%",
                    marginTop: "6px",
                    padding: "9px 10px",
                    fontSize: "13px",
                    border: `1px solid ${theme.border}`,
                    borderRadius: "8px",
                    fontFamily: "'Inter', system-ui, sans-serif",
                    color: theme.textPrimary,
                    background: theme.cardBg,
                    opacity: isDisrupting ? 0.6 : 1,
                    cursor: isDisrupting ? "default" : "pointer",
                  }}
                >
                  {RANKING_MODES.map(m => (
                    <option key={m.value} value={m.value}>{m.label}</option>
                  ))}
                </select>
              </div>

              <button
                onClick={handleCancel}
                disabled={isDisrupting || !canCancel}
                style={{
                  marginTop: "16px",
                  width: "100%",
                  padding: "10px",
                  border: `1px solid ${theme.border}`,
                  borderRadius: "8px",
                  background: theme.cardBg,
                  color: theme.textPrimary,
                  fontSize: "13px",
                  fontWeight: 600,
                  fontFamily: "'Inter', system-ui, sans-serif",
                  opacity: isDisrupting || !canCancel ? 0.6 : 1,
                  cursor: isDisrupting || !canCancel ? "default" : "pointer",
                }}
              >
                {isDisrupting
                  ? "Working..."
                  : canCancel
                    ? "Cancel this flight"
                    : "Not yet departed — nothing to cancel"}
              </button>

              <div style={{ marginTop: "20px" }}>
                <label style={{ fontSize: "11px", fontWeight: 700, color: theme.textSecondary, textTransform: "uppercase", letterSpacing: "0.04em" }}>
                  Solver
                </label>
                <select
                  value={solver}
                  onChange={e => setSolver(e.target.value)}
                  disabled={isGrounding}
                  style={{
                    width: "100%",
                    marginTop: "6px",
                    padding: "9px 10px",
                    fontSize: "13px",
                    border: `1px solid ${theme.border}`,
                    borderRadius: "8px",
                    fontFamily: "'Inter', system-ui, sans-serif",
                    color: theme.textPrimary,
                    background: theme.cardBg,
                    opacity: isGrounding ? 0.6 : 1,
                    cursor: isGrounding ? "default" : "pointer",
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
                  marginTop: "8px",
                  width: "100%",
                  padding: "10px",
                  border: `1px solid ${theme.accentColor}`,
                  borderRadius: "8px",
                  background: theme.cardBg,
                  color: theme.accentColor,
                  fontSize: "13px",
                  fontWeight: 600,
                  fontFamily: "'Inter', system-ui, sans-serif",
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
                      background: theme.successBg,
                      border: `1px solid ${theme.successBorder}`,
                      borderRadius: "8px",
                      padding: "14px",
                    }}
                  >
                    <div style={{ fontSize: "11px", fontWeight: 700, color: theme.successNumber, textTransform: "uppercase", letterSpacing: "0.03em" }}>
                      Recovery coverage · {aircraftDownResult.solver} solver
                    </div>
                    <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "26px", fontWeight: 800, color: theme.successNumber, marginTop: "4px" }}>
                      {aircraftDownResult.total_covered_legs} / {aircraftDownResult.total_orphaned_legs} legs covered
                    </div>
                    <div style={{ fontSize: "11px", color: theme.textSecondary, marginTop: "6px" }}>
                      {aircraftDownResult.total_uncovered_legs} leg(s) uncovered · {aircraftDownResult.num_segments} segment(s) ·{" "}
                      {aircraftDownResult.total_induced_delay_minutes.toFixed(0)} min total induced delay
                    </div>
                  </div>

                  <h3 style={{ fontSize: "11px", fontWeight: 700, color: theme.textPrimary, textTransform: "uppercase", letterSpacing: "0.04em", marginTop: "20px", marginBottom: "10px" }}>
                    Recovery segments
                  </h3>

                  {aircraftDownResult.segments.map((seg, i) => (
                    seg.tail_number ? (
                      <div
                        key={i}
                        style={{
                          border: `1px solid ${theme.border}`,
                          borderRadius: "8px",
                          padding: "10px 12px",
                          marginBottom: "8px",
                          background: theme.cardBg,
                        }}
                      >
                        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                          <span style={{ fontFamily: "'JetBrains Mono', monospace", fontWeight: 700, fontSize: "13px", color: theme.textPrimary }}>
                            {seg.tail_number}
                          </span>
                        </div>
                        <div style={{ fontSize: "11px", color: theme.textSecondary, marginTop: "4px" }}>
                          {formatLegs(seg.legs_covered)}
                        </div>
                        <div style={{ fontSize: "11px", color: theme.textSecondary, marginTop: "4px" }}>
                          {seg.induced_delay_minutes != null ? `Delay: ${seg.induced_delay_minutes.toFixed(0)} min · ` : ""}
                          {seg.score != null ? `Score: ${seg.score.toFixed(2)}` : ""}
                        </div>
                      </div>
                    ) : (
                      <div
                        key={i}
                        style={{
                          border: `1px solid ${theme.dangerBorder}`,
                          borderRadius: "8px",
                          padding: "10px 12px",
                          marginBottom: "8px",
                          background: theme.dangerBg,
                        }}
                      >
                        <div style={{ fontSize: "13px", fontWeight: 700, color: theme.dangerText }}>
                          No substitute found for {formatLegs(seg.legs_covered)}
                        </div>
                      </div>
                    )
                  ))}
                </div>
              )}

              {disruptError && (
                <div
                  style={{
                    marginTop: "20px",
                    border: `1px solid ${theme.dangerBorder}`,
                    borderRadius: "8px",
                    padding: "14px",
                    background: theme.dangerBg,
                  }}
                >
                  <div style={{ fontSize: "13px", fontWeight: 700, color: theme.dangerText }}>
                    {typeof disruptError === "string" ? disruptError : "Failed to cancel this flight."}
                  </div>
                </div>
              )}

              {disruptResult && (
                <div style={{ marginTop: "20px" }}>
                  {/* Headline improvement number, with honest supporting detail underneath -- never collapsed into just the percentage alone. */}
                  <div
                    style={{
                      background: theme.successBg,
                      border: `1px solid ${theme.successBorder}`,
                      borderRadius: "8px",
                      padding: "14px",
                    }}
                  >
                    <div style={{ fontSize: "11px", fontWeight: 700, color: theme.successNumber, textTransform: "uppercase", letterSpacing: "0.03em" }}>
                      Network disruption avoided
                    </div>
                    <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "26px", fontWeight: 800, color: theme.successNumber, marginTop: "4px" }}>
                      {disruptResult.improvement_pct !== null ? `${disruptResult.improvement_pct.toFixed(1)}%` : "—"}
                    </div>
                    <div style={{ fontSize: "11px", color: theme.textSecondary, marginTop: "6px" }}>
                      {disruptResult.cancellations_avoided} leg(s) kept flying instead of cancelled ·{" "}
                      {disruptResult.total_induced_delay_minutes !== null
                        ? `${disruptResult.total_induced_delay_minutes.toFixed(0)} min induced delay`
                        : "no substitute available"}
                    </div>
                    <div style={{ fontSize: "10px", color: theme.textMuted, marginTop: "6px" }}>
                      Headline % assumes a 300 min modeling estimate per avoided cancellation — a stated assumption, not derived from data.
                    </div>
                  </div>

                  <h3 style={{ fontSize: "11px", fontWeight: 700, color: theme.textPrimary, textTransform: "uppercase", letterSpacing: "0.04em", marginTop: "20px", marginBottom: "10px" }}>
                    Ranked candidates
                  </h3>

                  {disruptResult.ranked_candidates.length === 0 && (
                    <div style={{ fontSize: "13px", color: theme.textMuted }}>
                      No viable substitute found — these legs would be cancelled.
                    </div>
                  )}

                  {disruptResult.ranked_candidates.map((c, i) => (
                    <div
                      key={c.tail_number}
                      style={{
                        border: i === 0 ? `2px solid ${theme.accentColor}` : `1px solid ${theme.border}`,
                        borderRadius: "8px",
                        padding: "10px 12px",
                        marginBottom: "8px",
                        background: i === 0 ? "#eaefff" : theme.cardBg,
                      }}
                    >
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                        <span style={{ fontFamily: "'JetBrains Mono', monospace", fontWeight: 700, fontSize: "13px", color: i === 0 ? "#14181f" : theme.textPrimary }}>
                          {c.tail_number}
                        </span>
                        {i === 0 && (
                          <span
                            style={{
                              fontSize: "9.5px",
                              fontWeight: 700,
                              color: "#ffffff",
                              background: theme.accentColor,
                              borderRadius: "10px",
                              padding: "2px 8px",
                              textTransform: "uppercase",
                              letterSpacing: "0.03em",
                            }}
                          >
                            Recommended
                          </span>
                        )}
                      </div>
                      <div style={{ fontSize: "11px", color: i === 0 ? "#5b6472" : theme.textSecondary, marginTop: "4px" }}>
                        Delay: {c.delay.toFixed(0)} min · Network disruption: {c.network_disruption.toFixed(0)} min · Connections at risk: {c.connecting_flights_missed}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </>
          ) : (
            <div>
              <div style={{ fontSize: "11px", fontWeight: 700, color: theme.textSecondary, textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: "10px" }}>
                Find a flight
              </div>
              <AirportAutocomplete
                value={searchOrigin}
                onChangeText={t => setSearchOrigin(t.toUpperCase())}
                onSelect={setSearchOrigin}
                airports={airportOptions}
                placeholder="Origin"
                style={{ marginBottom: "6px" }}
              />
              <AirportAutocomplete
                value={searchDest}
                onChangeText={t => setSearchDest(t.toUpperCase())}
                onSelect={setSearchDest}
                airports={airportOptions}
                placeholder="Destination"
                style={{ marginBottom: "8px" }}
              />
              <button
                onClick={() => setEligibleOnly(v => !v)}
                style={{
                  border: eligibleOnly ? `1px solid ${theme.successBorder}` : `1px solid ${theme.border}`,
                  background: eligibleOnly ? theme.successBg : theme.cardBg,
                  color: eligibleOnly ? theme.successNumber : theme.textSecondary,
                  borderRadius: "20px",
                  padding: "6px 14px",
                  fontSize: "11.5px",
                  fontWeight: 600,
                  fontFamily: "'Inter', system-ui, sans-serif",
                  cursor: "pointer",
                }}
              >
                {eligibleOnly ? "● Eligible for disruption" : "Eligible for disruption"}
              </button>

              {searchActive ? (
                <div style={{ marginTop: "16px" }}>
                  <div style={{ fontSize: "10px", color: theme.textMuted, marginBottom: "8px" }}>
                    {searchResults.length} match{searchResults.length === 1 ? "" : "es"} · reflects each aircraft's current leg only
                  </div>
                  {searchResults.length === 0 && (
                    <div style={{ fontSize: "13px", color: theme.textMuted }}>
                      No aircraft currently match.
                    </div>
                  )}
                  {searchResults.map(a => (
                    <div
                      key={a.tail_number}
                      onClick={() => handleAircraftClick(a)}
                      style={{
                        cursor: "pointer",
                        border: `1px solid ${theme.border}`,
                        borderRadius: "8px",
                        padding: "10px 12px",
                        marginBottom: "8px",
                        background: theme.cardBg,
                      }}
                    >
                      <span style={{ fontFamily: "'JetBrains Mono', monospace", fontWeight: 700, fontSize: "13px", color: theme.textPrimary }}>
                        {a.tail_number}
                      </span>
                      <div style={{ fontSize: "11px", color: theme.textSecondary, marginTop: "4px" }}>
                        {a.origin ?? "—"} → {a.destination ?? "—"} · {a.status}
                      </div>
                    </div>
                  ))}
                </div>
              ) : demoMode ? (
                <div style={{ marginTop: "16px" }}>
              {DEMO_EXAMPLES.map(ex => (
                <button
                  key={ex.tail_number + ex.date + ex.time}
                  onClick={() => goToDemoExample(ex)}
                  style={{
                    display: "block",
                    width: "100%",
                    textAlign: "left",
                    border: `1px solid ${theme.border}`,
                    borderRadius: "8px",
                    padding: "10px 12px",
                    marginBottom: "8px",
                    background: theme.cardBg,
                    cursor: "pointer",
                    fontFamily: "'Inter', system-ui, sans-serif",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <span style={{ fontFamily: "'JetBrains Mono', monospace", fontWeight: 700, fontSize: "13px", color: theme.textPrimary }}>
                      {ex.label}
                    </span>
                    <span
                      style={{
                        fontSize: "9.5px",
                        fontWeight: 700,
                        color: ex.feature === "cancel" ? theme.accentColor : theme.successNumber,
                        background: ex.feature === "cancel" ? "#eaefff" : theme.successBg,
                        borderRadius: "10px",
                        padding: "2px 8px",
                        textTransform: "uppercase",
                        letterSpacing: "0.03em",
                      }}
                    >
                      {ex.feature === "cancel" ? "Cancel" : "Ground"}
                    </span>
                  </div>
                  <div style={{ fontSize: "11px", color: theme.textSecondary, marginTop: "4px", lineHeight: 1.4 }}>
                    {ex.blurb}
                  </div>
                </button>
              ))}
                </div>
              ) : (
                <div
                  style={{
                    marginTop: "24px",
                    display: "flex",
                    flexDirection: "column",
                    alignItems: "center",
                    textAlign: "center",
                    gap: "10px",
                    color: theme.textMuted,
                  }}
                >
                  <div style={{ fontSize: "32px" }} aria-hidden="true">✈</div>
                  <div style={{ fontSize: "12.5px", lineHeight: 1.5, maxWidth: "220px" }}>
                    Select an aircraft on the map, or search above, to explore recovery options
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default Live
