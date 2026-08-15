import { useState, useEffect, useRef, useCallback, useMemo } from "react"
import MapView from "../components/MapView"
import LiveSidebar from "../components/LiveSidebar"
import { useTheme } from "../ThemeContext"

const DEFAULT_MINUTES = 8 * 60 // 08:00
const MAX_MINUTES = 1439
// Playback speed: simulated minutes advanced per real millisecond while
// playing (unchanged from the original 5-min-per-500ms rate) -- applied
// continuously via requestAnimationFrame instead of a fixed-step interval,
// so dot movement is smooth regardless of frame timing.
const STEP_MINUTES = 5
const TICK_MS = 500
const PLAYBACK_RATE_MIN_PER_MS = STEP_MINUTES / TICK_MS
// The full day's schedule is fetched once per replayDate (see the
// scheduleByTail effect) -- there's no backend poll left to rate-limit.
// This only paces the local searchResults snapshot (see searchSnapshot)
// so LiveSidebar isn't handed a new array reference every animation frame.
const SEARCH_SNAPSHOT_REFRESH_MS = 1000

// Mirrors backend/analysis/geo.py's great_circle_interpolate exactly, so
// the frontend can smoothly re-derive an in_flight aircraft's position for
// any moment, not just at fetched keyframes.
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
  return [toDeg(Math.atan2(z, Math.hypot(x, y))), toDeg(Math.atan2(y, x))]
}

// Statuses where the aircraft hasn't left `origin` yet -- position it there.
// taxiing_in/parked position at `destination`; in_flight is interpolated
// separately. Mirrors backend/main.py's ORIGIN_STATUSES exactly.
const ORIGIN_STATUSES = new Set(["not_yet_started", "taxiing_out", "cancelled"])

// The three functions below port analysis/queries.py's find_owning_leg /
// classify_aircraft_status / get_aircraft_state to JS field-for-field, so
// the frontend can derive any aircraft's status at any instant from the
// once-per-date /schedule fetch instead of polling /state. Operate on
// epoch-ms (pre-parsed once when the schedule arrives, not per frame) --
// same principle as greatCircleInterpolate's inputs.
function findOwningLeg(legs, queryMs) {
  let owning = null
  for (const leg of legs) {
    if (leg.depMs != null && leg.depMs <= queryMs) owning = leg
    else break
  }
  return owning
}

function classifyStatus(leg, queryMs) {
  if (leg.cancelled) return "cancelled"
  if (leg.arrMs == null) return "in_flight"
  if (leg.wheelsOffMs != null && queryMs < leg.wheelsOffMs) return "taxiing_out"
  if (leg.wheelsOnMs != null && queryMs < leg.wheelsOnMs) return "in_flight"
  if (queryMs < leg.arrMs) return "taxiing_in"
  return "parked"
}

function getAircraftState(legs, queryMs) {
  if (!legs.length) {
    return { status: "not_operating", origin: null, destination: null, wheelsOffMs: null, wheelsOnMs: null }
  }
  const owning = findOwningLeg(legs, queryMs)
  if (!owning) {
    return { status: "not_yet_started", origin: legs[0].origin, destination: null, wheelsOffMs: null, wheelsOnMs: null }
  }
  return {
    status: classifyStatus(owning, queryMs),
    origin: owning.origin,
    destination: owning.dest,
    wheelsOffMs: owning.wheelsOffMs,
    wheelsOnMs: owning.wheelsOnMs,
  }
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

// Aircraft not currently in flight -- i.e. plausible to ground right now.
const ELIGIBLE_STATUSES = new Set(["taxiing_out", "taxiing_in", "parked", "not_yet_started"])

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
    body: "Click any aircraft dot on the map to open its detail panel on the right. Dot color shows status: see the legend in the bottom-left of the map.",
  },
  {
    title: "Cancel this flight",
    body: "Pick a ranking mode, then click \"Cancel this flight\" to see which other aircraft could realistically cover the leg, ranked by that mode, plus the modeled improvement in network disruption.",
  },
  {
    title: "Ground this aircraft",
    body: "Pick a solver (Greedy is fast, Optimal searches harder for a better answer with a timeout), then click \"Ground this aircraft\" to see how many of that tail's remaining legs the network can recover with substitute aircraft, and which (if any) are left uncovered.",
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
  // {[tail_number]: [{origin, dest, depMs, wheelsOffMs, wheelsOnMs, arrMs, cancelled}, ...]}
  // for the current replayDate, fetched once (see the schedule-fetch effect)
  // instead of polled -- see findOwningLeg/classifyStatus/getAircraftState.
  const [scheduleByTail, setScheduleByTail] = useState(null)
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
  // The DEMO_EXAMPLES entry we're waiting on the schedule-derived aircraft
  // list for.
  const [pendingDemoExample, setPendingDemoExample] = useState(null)
  const scheduleRequestId = useRef(0)
  // Updated every render (see the `visibleMinutesRef.current = visibleMinutes`
  // assignment below) and read at call time by handleCancel/handleModeChange/
  // handleGroundAircraft -- NOT a useCallback dependency, since visibleMinutes
  // changes every animation frame while playing and adding it as a dep would
  // recreate these handlers every frame, defeating LiveSidebar's React.memo.
  const visibleMinutesRef = useRef(DEFAULT_MINUTES)
  const [airportOptions, setAirportOptions] = useState([])
  // Lower-frequency copy of renderedAircraft used only by searchResults --
  // see the searchSnapshot effect below for why this needs to exist
  // separately from renderedAircraft (which changes every animation frame).
  const [searchSnapshot, setSearchSnapshot] = useState(null)
  // searchOrigin/searchDest are the committed filter values (only set on
  // Enter or clicking a suggestion) -- originInput/destInput are the raw
  // typed text, which only drives the autocomplete suggestion list. Without
  // this split, every keystroke re-filtered all ~1015 aircraft against a
  // half-typed code (e.g. "J", "JF") and flickered the match list before the
  // user had actually picked an airport.
  const [searchOrigin, setSearchOrigin] = useState("")
  const [searchDest, setSearchDest] = useState("")
  const [originInput, setOriginInput] = useState("")
  const [destInput, setDestInput] = useState("")
  const [eligibleOnly, setEligibleOnly] = useState(false)

  useEffect(() => {
    fetch("/airports")
      .then(res => res.json())
      .then(data => setAirportOptions(data.airports))
  }, [])

  // The whole day's schedule is known in advance (this is a replay of
  // historical data, not live telemetry), so fetch it once per replayDate
  // instead of polling for a snapshot at the current instant every second --
  // findOwningLeg/classifyStatus/getAircraftState (above) then derive any
  // aircraft's status/position at any instant purely client-side, with zero
  // per-frame network dependency and zero status-transition latency.
  useEffect(() => {
    const requestId = ++scheduleRequestId.current
    fetch(`/schedule?date=${replayDate}`)
      .then(res => res.json())
      .then(data => {
        if (requestId !== scheduleRequestId.current) return
        const byTail = {}
        for (const t of data.tails) {
          byTail[t.tail_number] = t.legs.map(leg => ({
            origin: leg.origin,
            dest: leg.dest,
            depMs: leg.dep ? new Date(leg.dep).getTime() : null,
            wheelsOffMs: leg.wheels_off ? new Date(leg.wheels_off).getTime() : null,
            wheelsOnMs: leg.wheels_on ? new Date(leg.wheels_on).getTime() : null,
            arrMs: leg.arr ? new Date(leg.arr).getTime() : null,
            cancelled: leg.cancelled,
          }))
        }
        setScheduleByTail(byTail)
      })
  }, [replayDate])

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

  // Stable references (useState setters are guaranteed stable) -- passed to
  // memo()-wrapped LiveSidebar/AirportAutocomplete, so a fresh closure every
  // render would silently defeat the memo.
  const closePanel = useCallback(() => {
    setSelectedAircraft(null)
    setDisruptResult(null)
    setDisruptError(null)
    setAircraftDownResult(null)
  }, [])

  // Aircraft that haven't started their first leg yet have no destination
  // (see getAircraftState -- only `origin` is populated), so there's
  // nothing to cancel.
  const canCancel = selectedAircraft?.destination != null

  const handleCancel = useCallback(() => {
    if (isDisrupting || !canCancel) return
    setIsDisrupting(true)
    fetch("/disrupt", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tail_number: selectedAircraft.tail_number,
        date: replayDate,
        time: minutesToHHMM(Math.round(visibleMinutesRef.current)),
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
  }, [isDisrupting, canCancel, selectedAircraft, replayDate, selectedMode])

  // Changing mode after a result already exists re-runs the same disruption
  // under the new weighting -- simplest is to just re-fetch automatically,
  // consistent with how the rest of this file re-fetches on state change.
  const handleModeChange = useCallback((e) => {
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
          time: minutesToHHMM(Math.round(visibleMinutesRef.current)),
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
  }, [isDisrupting, disruptResult, selectedAircraft, replayDate])

  const handleGroundAircraft = useCallback(() => {
    if (isGrounding) return
    setAircraftDownResult(null)
    setIsGrounding(true)
    fetch("/disrupt/aircraft-down", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tail_number: selectedAircraft.tail_number,
        date: replayDate,
        time: minutesToHHMM(Math.round(visibleMinutesRef.current)),
        mode: selectedMode,
        solver: solver,
      }),
    })
      .then(res => res.json())
      .then(data => setAircraftDownResult(data))
      .finally(() => setIsGrounding(false))
  }, [isGrounding, selectedAircraft, replayDate, selectedMode, solver])

  const toggleDemoMode = () => setDemoMode(d => !d)
  const openTutorial = () => setTutorialOpen(true)
  const closeTutorial = () => setTutorialOpen(false)

  const goToDemoExample = useCallback((example) => {
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
  }, [])

  const toggleEligibleOnly = useCallback(() => setEligibleOnly(v => !v), [])

  const handleOriginChangeText = useCallback((t) => {
    const upper = t.toUpperCase()
    setOriginInput(upper)
    if (!upper) setSearchOrigin("")
  }, [])
  const handleOriginSelect = useCallback((code) => {
    setOriginInput(code)
    setSearchOrigin(code)
  }, [])
  const handleDestChangeText = useCallback((t) => {
    const upper = t.toUpperCase()
    setDestInput(upper)
    if (!upper) setSearchDest("")
  }, [])
  const handleDestSelect = useCallback((code) => {
    setDestInput(code)
    setSearchDest(code)
  }, [])

  // What's actually shown: driven by the smooth clock while playing, by the
  // slider's raw drag position otherwise.
  const visibleMinutes = playing ? displayMinutes : draftMinutes
  visibleMinutesRef.current = visibleMinutes

  // {[Origin]: [lat, lon]}, built once from the already-fetched airport
  // list (it already carries coords for the autocomplete) so position
  // resolution below doesn't need its own network round-trip.
  const airportCoords = useMemo(() => {
    const coords = {}
    for (const a of airportOptions) coords[a.Origin] = [a.lat, a.lon]
    return coords
  }, [airportOptions])

  // Derives every aircraft's status + position at the exact current instant
  // from the once-per-date schedule, mirroring backend/main.py's /state
  // loop (main.py:288-341) field-for-field: getAircraftState for
  // status/origin/destination, then the same in_flight-interpolate-else-
  // position-at-airport rule. Runs every frame while playing (status can
  // change any frame), but each tail's own leg list is tiny (a handful of
  // legs) and was pre-parsed to epoch-ms once when the schedule arrived --
  // no per-frame Date parsing or network involved.
  const renderedAircraft = useMemo(() => {
    if (!scheduleByTail) return null
    const queryMs = minutesToDate(replayDate, visibleMinutes).getTime()
    const results = []
    for (const tailNumber in scheduleByTail) {
      const legs = scheduleByTail[tailNumber]
      const { status, origin, destination, wheelsOffMs, wheelsOnMs } = getAircraftState(legs, queryMs)

      let lat = null, lon = null
      if (status === "in_flight") {
        const hasTiming = wheelsOffMs != null && wheelsOnMs != null && wheelsOnMs > wheelsOffMs
        const originCoord = airportCoords[origin]
        const destCoord = airportCoords[destination]
        if (hasTiming && originCoord && destCoord) {
          const frac = Math.max(0, Math.min(1, (queryMs - wheelsOffMs) / (wheelsOnMs - wheelsOffMs)))
          ;[lat, lon] = greatCircleInterpolate(originCoord[0], originCoord[1], destCoord[0], destCoord[1], frac)
        } else if (destCoord) {
          [lat, lon] = destCoord
        }
      } else {
        const positionCode = ORIGIN_STATUSES.has(status) ? origin : destination
        const coord = airportCoords[positionCode]
        if (coord) [lat, lon] = coord
      }

      results.push({ tail_number: tailNumber, status, origin, destination, lat, lon })
    }
    return results
  }, [scheduleByTail, airportCoords, replayDate, visibleMinutes])

  // Updated every render so the interval below always reads the CURRENT
  // renderedAircraft, not whatever it closed over when the interval was
  // created -- a plain closure over renderedAircraft would capture one
  // stale snapshot forever, since this effect only re-runs on `playing`
  // changes, not every frame.
  const renderedAircraftRef = useRef(null)
  renderedAircraftRef.current = renderedAircraft

  // searchResults reads searchSnapshot (refreshed at most once a second, see
  // below), not renderedAircraft directly -- renderedAircraft gets a new
  // array reference every animation frame while playing, and searchResults
  // is a prop into memo()-wrapped LiveSidebar, so riding renderedAircraft
  // directly would hand it a "changed" prop every frame and defeat the
  // memo, the same class of bug the original searchResults rebase (earlier
  // this session) fixed for the old poll-driven aircraftState.
  useEffect(() => {
    setSearchSnapshot(renderedAircraftRef.current)
  }, [scheduleByTail, replayDate])

  useEffect(() => {
    if (!playing) {
      setSearchSnapshot(renderedAircraftRef.current)
      return
    }
    const interval = setInterval(() => setSearchSnapshot(renderedAircraftRef.current), SEARCH_SNAPSHOT_REFRESH_MS)
    return () => clearInterval(interval)
  }, [playing])

  // Once the schedule-derived aircraft list resolves for the demo example's
  // date/time, select the real aircraft object -- same as clicking it on
  // the map -- but keep the example's specific origin/destination (the leg
  // being evaluated), since that can differ from wherever the aircraft
  // physically is at that exact query time.
  useEffect(() => {
    if (!pendingDemoExample || !renderedAircraft) return
    const match = renderedAircraft.find(a => a.tail_number === pendingDemoExample.tail_number)
    if (match) {
      setSelectedAircraft(
        pendingDemoExample.origin
          ? { ...match, origin: pendingDemoExample.origin, destination: pendingDemoExample.destination }
          : match
      )
      setPendingDemoExample(null)
    }
  }, [renderedAircraft, pendingDemoExample])

  const searchActive = Boolean(searchOrigin || searchDest || eligibleOnly)
  const searchResults = useMemo(() => {
    if (!searchActive || !searchSnapshot) return []
    return searchSnapshot.filter(a =>
      (!searchOrigin || a.origin === searchOrigin) &&
      (!searchDest || a.destination === searchDest) &&
      (!eligibleOnly || ELIGIBLE_STATUSES.has(a.status))
    )
  }, [searchSnapshot, searchOrigin, searchDest, eligibleOnly, searchActive])

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
            type="button"
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
            type="button"
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
            type="button"
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
                type="button"
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
          {(scheduleByTail === null || airportOptions.length === 0) && (
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
            colorRevision={searchSnapshot}
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

        <LiveSidebar
          selectedAircraft={selectedAircraft}
          closePanel={closePanel}
          selectedMode={selectedMode}
          handleModeChange={handleModeChange}
          isDisrupting={isDisrupting}
          canCancel={canCancel}
          handleCancel={handleCancel}
          solver={solver}
          setSolver={setSolver}
          isGrounding={isGrounding}
          handleGroundAircraft={handleGroundAircraft}
          aircraftDownResult={aircraftDownResult}
          disruptError={disruptError}
          disruptResult={disruptResult}
          demoMode={demoMode}
          searchActive={searchActive}
          searchResults={searchResults}
          handleAircraftClick={handleAircraftClick}
          originInput={originInput}
          destInput={destInput}
          onOriginChangeText={handleOriginChangeText}
          onOriginSelect={handleOriginSelect}
          onDestChangeText={handleDestChangeText}
          onDestSelect={handleDestSelect}
          airportOptions={airportOptions}
          eligibleOnly={eligibleOnly}
          toggleEligibleOnly={toggleEligibleOnly}
          goToDemoExample={goToDemoExample}
        />
      </div>
    </div>
  )
}

export default Live
