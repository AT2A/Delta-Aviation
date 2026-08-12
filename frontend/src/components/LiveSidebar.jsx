import { memo } from "react"
import { useTheme } from "../ThemeContext"
import AirportAutocomplete from "./AirportAutocomplete"

const RANKING_MODES = [
  { value: "minimize_this_flight_delay", label: "Minimize this flight's delay" },
  { value: "minimize_total_delay", label: "Minimize total propagated delay" },
  { value: "protect_other_flights", label: "Protect other flights" },
  { value: "protect_major_flights", label: "Protect major flights" },
  { value: "all_factors_combined", label: "All factors combined" },
]

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

const formatLegs = (legs) => legs.map(([u, v]) => `${u}→${v}`).join(", ")

// Everything in the right-hand panel: aircraft detail/Cancel/Ground and
// their results, the "Find a flight" search panel, and the Demo Mode list.
// Deliberately takes no prop that changes every animation frame (no
// displayMinutes/visibleMinutes) and is wrapped in memo() so it doesn't
// re-render 60x/sec while Live Replay is playing -- only MapView's aircraft
// dots need to update that often.
function LiveSidebar({
  selectedAircraft,
  closePanel,
  selectedMode,
  handleModeChange,
  isDisrupting,
  canCancel,
  handleCancel,
  solver,
  setSolver,
  isGrounding,
  handleGroundAircraft,
  aircraftDownResult,
  disruptError,
  disruptResult,
  demoMode,
  searchActive,
  searchResults,
  handleAircraftClick,
  originInput,
  destInput,
  onOriginChangeText,
  onOriginSelect,
  onDestChangeText,
  onDestSelect,
  airportOptions,
  eligibleOnly,
  toggleEligibleOnly,
  goToDemoExample,
}) {
  const { theme } = useTheme()

  return (
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
            value={originInput}
            onChangeText={onOriginChangeText}
            onSelect={onOriginSelect}
            airports={airportOptions}
            placeholder="Origin"
            style={{ marginBottom: "6px" }}
          />
          <AirportAutocomplete
            value={destInput}
            onChangeText={onDestChangeText}
            onSelect={onDestSelect}
            airports={airportOptions}
            placeholder="Destination"
            style={{ marginBottom: "8px" }}
          />
          <button
            onClick={toggleEligibleOnly}
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
  )
}

export default memo(LiveSidebar)
