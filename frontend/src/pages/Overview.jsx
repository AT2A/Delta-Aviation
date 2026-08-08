import { useState, useEffect, useMemo } from "react"
import MapView from "../components/MapView"
import BottleneckPanel from "../components/BottleneckPanel"
import AirportAutocomplete from "../components/AirportAutocomplete"
import { useTheme } from "../ThemeContext"

function Swatch({ color, round }) {
  return (
    <span style={{
      display: "inline-block",
      width: round ? "10px" : "14px",
      height: round ? "10px" : "3px",
      background: color,
      borderRadius: round ? "50%" : 0,
      marginRight: "6px",
      verticalAlign: "middle",
    }} />
  )
}

function Overview() {
  const { theme } = useTheme()
  const [airports, setAirports] = useState([])
  const [tails, setTails] = useState([])
  const [airportQuery, setAirportQuery] = useState("")
  const [selectedAirport, setSelectedAirport] = useState(null)
  const [inheritedPct, setInheritedPct] = useState(null)

  useEffect(() => {
    fetch("/airports")
      .then(res => res.json())
      .then(data => {
        setAirports(data.airports)
        setInheritedPct(data.network_inherited_delay_pct)
      })

    fetch("/tails")
      .then(res => res.json())
      .then(data => setTails(data.tails))
  }, [])

  // Exact, not approximate: every flight has exactly one Origin airport, and
  // /airports returns every airport (unfiltered), so this sum equals the
  // true total flight-leg count.
  const totalFlights = useMemo(
    () => airports.reduce((sum, a) => sum + (a.total_departures || 0), 0),
    [airports]
  )
  const uniqueTailCount = tails.length

  const flightsLabel = useMemo(
    () => new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(totalFlights),
    [totalFlights]
  )
  const tailsLabel = useMemo(
    () => new Intl.NumberFormat("en-US").format(uniqueTailCount),
    [uniqueTailCount]
  )

  const cardStyle = {
    background: theme.cardBg,
    border: `1px solid ${theme.border}`,
    borderRadius: "4px",
    boxSizing: "border-box",
  }

  return (
    <div style={{
      background: theme.pageBg,
      minHeight: "calc(100vh - 64px)",
      color: theme.textPrimary,
      fontFamily: "'Inter', system-ui, sans-serif",
      display: "flex",
      flexDirection: "column",
    }}>
      <div style={{ padding: "28px 32px", display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }}>

        {/* Row 1: headline stat + small tiles */}
        <div style={{ display: "flex", gap: "20px", marginBottom: "20px", flexShrink: 0 }}>
          <div style={{ ...cardStyle, flex: 2, padding: "22px 26px", display: "flex", alignItems: "center", gap: "26px" }}>
            <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "48px", fontWeight: 700, color: theme.accentColor, lineHeight: 1 }}>
              {inheritedPct != null ? `${inheritedPct}%` : "—"}
            </div>
            <div style={{ fontSize: "13.5px", color: theme.textSecondary, maxWidth: "520px", lineHeight: 1.5 }}>
              of Delta's 2025 delay minutes were <strong style={{ color: theme.textPrimary }}>inherited</strong> from
              a prior late flight, not caused fresh at the departure airport.
            </div>
          </div>
          <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "12px" }}>
            <div style={{ ...cardStyle, flex: 1, padding: "12px 18px", display: "flex", flexDirection: "column", justifyContent: "center" }}>
              <div style={{ fontSize: "19px", fontWeight: 700, fontFamily: "'JetBrains Mono', monospace" }}>
                {flightsLabel}
              </div>
              <div style={{ fontSize: "11px", color: theme.textMuted }}>flights analyzed</div>
            </div>
            <div style={{ ...cardStyle, flex: 1, padding: "12px 18px", display: "flex", flexDirection: "column", justifyContent: "center" }}>
              <div style={{ fontSize: "19px", fontWeight: 700, fontFamily: "'JetBrains Mono', monospace" }}>
                {tailsLabel}
              </div>
              <div style={{ fontSize: "11px", color: theme.textMuted }}>unique tail numbers</div>
            </div>
          </div>
        </div>

        {/* Row 2: bottleneck table + route network map */}
        <div style={{ display: "flex", gap: "20px", alignItems: "flex-start", flex: 1, minHeight: 0 }}>
          <BottleneckPanel airports={airports} />

          <div style={{ ...cardStyle, flex: 1, minWidth: 0, padding: "20px 24px", alignSelf: "stretch", display: "flex", flexDirection: "column", minHeight: 0 }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "12px", flexShrink: 0, gap: "16px" }}>
              <div style={{ fontSize: "13px", fontWeight: 700, whiteSpace: "nowrap" }}>Delta Route Network</div>

              <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                <AirportAutocomplete
                  value={airportQuery}
                  onChangeText={t => {
                    const v = t.toUpperCase()
                    setAirportQuery(v)
                    setSelectedAirport(airports.some(a => a.Origin === v) ? v : null)
                  }}
                  onSelect={code => { setAirportQuery(code); setSelectedAirport(code) }}
                  airports={airports}
                  placeholder="Search airport…"
                  style={{ width: "170px" }}
                />
                {selectedAirport && (
                  <button
                    onClick={() => { setSelectedAirport(null); setAirportQuery("") }}
                    style={{ border: "none", background: "none", cursor: "pointer", fontSize: "16px", color: theme.textMuted, lineHeight: 1, padding: 0 }}
                  >
                    &times;
                  </button>
                )}
              </div>

              <div style={{ fontSize: "10.5px", color: theme.textMuted, fontFamily: "'JetBrains Mono', monospace", whiteSpace: "nowrap" }}>
                RENDERED VIA DECK.GL &middot; LIVE NETWORK DATA
              </div>
            </div>

            <div style={{ borderRadius: "2px", overflow: "hidden", flex: 1, minHeight: 0 }}>
              <MapView height="100%" selectedAirport={selectedAirport} />
            </div>

            <div style={{ display: "flex", gap: "20px", marginTop: "14px", fontSize: "11px", color: theme.textSecondary, flexWrap: "wrap", flexShrink: 0 }}>
              <div style={{ display: "flex", alignItems: "center" }}>
                <Swatch color="#5090ff" /> Route: avg delay &le; 15min
              </div>
              <div style={{ display: "flex", alignItems: "center" }}>
                <Swatch color="#ff5050" /> Route: avg delay &gt; 15min
              </div>
              <div style={{ display: "flex", alignItems: "center" }}>
                <Swatch color="#64c8ff" round /> Airport: relay rate &le; 12%
              </div>
              <div style={{ display: "flex", alignItems: "center" }}>
                <Swatch color="#ff6464" round /> Airport: relay rate &gt; 12%
              </div>
            </div>
          </div>

          {selectedAirport && (() => {
            const a = airports.find(x => x.Origin === selectedAirport)
            if (!a) return null
            return (
              <div style={{
                ...cardStyle, flex: "0 0 280px", padding: "20px 24px",
                alignSelf: "stretch", fontFamily: "'Inter', system-ui, sans-serif",
              }}>
                <div style={{ fontFamily: "'JetBrains Mono', monospace", fontWeight: 700, fontSize: "22px", color: theme.textPrimary }}>
                  {a.Origin}
                </div>
                <span style={{
                  display: "inline-block", marginTop: "6px", fontSize: "9.5px", fontWeight: 700,
                  borderRadius: "10px", padding: "2px 8px",
                  color: a.is_reliable ? theme.successNumber : theme.textMuted,
                  background: a.is_reliable ? theme.successBg : theme.pillTrackBg,
                }}>
                  {a.is_reliable ? "RELIABLE SAMPLE" : "LOW SAMPLE"}
                </span>

                <div style={{ marginTop: "18px", fontSize: "10px", color: theme.textMuted, textTransform: "uppercase", letterSpacing: "0.03em" }}>
                  Total Departures
                </div>
                <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "19px", fontWeight: 700 }}>
                  {a.total_departures.toLocaleString()}
                </div>

                <div style={{ marginTop: "14px", fontSize: "10px", color: theme.textMuted, textTransform: "uppercase", letterSpacing: "0.03em" }}>
                  Centrality
                </div>
                <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "19px", fontWeight: 700, color: theme.accentColor }}>
                  {(a.betweenness * 100).toFixed(1)}%
                </div>

                <div style={{ marginTop: "14px", fontSize: "10px", color: theme.textMuted, textTransform: "uppercase", letterSpacing: "0.03em" }}>
                  Relay Rate
                </div>
                <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "19px", fontWeight: 700, color: theme.textSecondary }}>
                  {(a.inheritance_rate * 100).toFixed(1)}%
                </div>
              </div>
            )
          })()}
        </div>

      </div>
    </div>
  )
}

export default Overview
