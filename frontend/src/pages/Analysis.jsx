import { useState, useEffect, useMemo } from "react"
import {
  ResponsiveContainer, ScatterChart, Scatter, XAxis, YAxis, ZAxis,
  CartesianGrid, Tooltip, Legend, LabelList,
} from "recharts"
import { useTheme } from "../ThemeContext"
import { API_BASE } from "../apiBase"

const ATL = "ATL"
const FL_CLUSTER = ["PBI", "FLL", "MIA", "TPA", "MCO"]

// FL cluster airports sit within ~4 points of inheritance_rate of each other at
// x=0, so stacked default labels collide. Fan them out horizontally with
// increasing offsets (+ a leader line back to the real point) instead.
const FL_LABEL_DX = { TPA: 40, MCO: 65, FLL: 90, MIA: 115, PBI: 140 }

// A function (not a rendered element) so recharts calls it directly with its
// computed {x, y, value} -- passing a rendered <FlClusterLabel fill=.../>
// element instead gets prop-merged by recharts' LabelList and the fill is
// clobbered, since recharts' own (unset) "fill" prop wins the merge.
function makeFlClusterLabel(fill) {
  return function FlClusterLabel({ x, y, value }) {
    const dx = FL_LABEL_DX[value] ?? 60
    return (
      <g>
        <line x1={x + 5} y1={y} x2={x + dx - 4} y2={y} stroke={fill} strokeWidth={1} strokeDasharray="2 2" opacity={0.5} />
        <text x={x + dx} y={y} dy={3.5} fontSize={10} fontWeight={700} fontFamily="'JetBrains Mono', monospace" fill={fill}>
          {value}
        </text>
      </g>
    )
  }
}

// Investigation findings from the disruption-recovery research sessions -- real
// numbers captured by running analysis/experiments/batch_disruption_check.py
// and analysis/disruption.py's greedy-vs-optimal demo on 2026-08-01. Not
// exposed by any live endpoint (the underlying scripts are one-off diagnostics,
// not wired into the backend), so these are stated as labeled historical
// findings rather than presented as live-computed.
const FINDINGS_DATE = "2026-08-08"

function CustomTooltip({ active, payload, theme }) {
  if (!active || !payload || !payload.length) return null
  const a = payload[0].payload
  return (
    <div style={{
      background: theme.cardBg,
      border: `1px solid ${theme.border}`,
      borderRadius: "4px",
      padding: "10px 14px",
      fontFamily: "'Inter', system-ui, sans-serif",
      fontSize: "12px",
      boxShadow: "0 4px 16px rgba(0,0,0,0.12)",
    }}>
      <div style={{ fontFamily: "'JetBrains Mono', monospace", fontWeight: 700, fontSize: "13px", marginBottom: "6px", color: theme.textPrimary }}>
        {a.Origin}
      </div>
      <div style={{ color: theme.textSecondary, marginBottom: "2px" }}>
        Centrality: <strong style={{ color: theme.textPrimary }}>{(a.betweenness * 100).toFixed(1)}%</strong>
      </div>
      <div style={{ color: theme.textSecondary, marginBottom: "2px" }}>
        Inheritance rate: <strong style={{ color: theme.textPrimary }}>{(a.inheritance_rate * 100).toFixed(1)}%</strong>
      </div>
      <div style={{ color: theme.textMuted, fontSize: "11px" }}>
        {a.total_departures.toLocaleString()} departures
      </div>
    </div>
  )
}

function Analysis() {
  const { theme, mode } = useTheme()
  const [airports, setAirports] = useState([])
  const [inheritedPct, setInheritedPct] = useState(null)
  const [cascadeStats, setCascadeStats] = useState(null)
  const [hideATL, setHideATL] = useState(false)

  useEffect(() => {
    fetch(`${API_BASE}/airports`)
      .then(res => res.json())
      .then(data => {
        setAirports(data.airports)
        setInheritedPct(data.network_inherited_delay_pct)
        setCascadeStats({
          total: data.network_total_cascades,
          avgDepth: data.network_avg_cascade_depth,
          maxDepth: data.network_max_cascade_depth,
        })
      })
  }, [])

  const { baseline, atlPoint, flPoints, atlCentrality, zeroBtwCount } = useMemo(() => {
    const baseline = airports.filter(a => a.Origin !== ATL && !FL_CLUSTER.includes(a.Origin))
    const atlPoint = airports.filter(a => a.Origin === ATL)
    const flPoints = airports.filter(a => FL_CLUSTER.includes(a.Origin))
    const atlCentrality = atlPoint.length ? atlPoint[0].betweenness : null
    const zeroBtwCount = airports.filter(a => a.is_zero_betweenness).length
    return { baseline, atlPoint, flPoints, atlCentrality, zeroBtwCount }
  }, [airports])

  // Passing an empty array (rather than filtering atlPoint out of the JSX)
  // means recharts' auto domain calculation naturally re-fits to the
  // remaining series -- no manual domain/ticks math needed.
  const atlScatterData = hideATL ? [] : atlPoint

  const cardStyle = {
    background: theme.cardBg,
    border: `1px solid ${theme.border}`,
    borderRadius: "4px",
    boxSizing: "border-box",
  }

  const eyebrowStyle = {
    fontSize: "10.5px",
    fontWeight: 700,
    color: theme.textMuted,
    textTransform: "uppercase",
    letterSpacing: "0.05em",
    fontFamily: "'JetBrains Mono', monospace",
    marginBottom: "4px",
  }

  const gridStroke = theme.borderLight
  const axisTickStyle = { fontSize: 11, fill: theme.textMuted, fontFamily: "'Inter', system-ui, sans-serif" }
  const atlColor = theme.accentColor
  const flColor = theme.dangerText

  return (
    <div style={{
      background: theme.pageBg,
      minHeight: "calc(100vh - 64px)",
      color: theme.textPrimary,
      fontFamily: "'Inter', system-ui, sans-serif",
      display: "flex",
      flexDirection: "column",
    }}>
      <div style={{ padding: "28px 32px", display: "flex", flexDirection: "column", gap: "20px" }}>

        <div>
          <div style={eyebrowStyle}>Phase 2 · Network Analysis</div>
        </div>

        {/* Centerpiece: centrality vs inheritance scatter */}
        <div style={{ ...cardStyle, padding: "24px 28px" }}>
          <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: "16px" }}>
            <div>
              <div style={{ fontSize: "15px", fontWeight: 700, marginBottom: "2px" }}>
                Betweenness Centrality vs. Inheritance Rate
              </div>
              <div style={{ fontSize: "12px", color: theme.textMuted, marginBottom: "18px" }}>
                One point per airport &middot; live data from /airports
              </div>
            </div>
            <button
              onClick={() => setHideATL(h => !h)}
              style={{
                border: `1px solid ${theme.border}`,
                background: hideATL ? theme.pillActiveBg : theme.cardBg,
                color: hideATL ? theme.pillActiveText : theme.textSecondary,
                borderRadius: "20px",
                padding: "6px 14px",
                fontSize: "11.5px",
                fontWeight: 600,
                fontFamily: "'Inter', system-ui, sans-serif",
                cursor: "pointer",
                whiteSpace: "nowrap",
                flexShrink: 0,
              }}
            >
              {hideATL ? "Show ATL" : "Hide ATL outlier"}
            </button>
          </div>

          {airports.length === 0 ? (
            <div style={{ height: 440, display: "flex", alignItems: "center", justifyContent: "center", color: theme.textMuted, fontSize: "13px" }}>
              Loading…
            </div>
          ) : (
          <ResponsiveContainer width="100%" height={440}>
            <ScatterChart key={hideATL ? "no-atl" : "with-atl"} margin={{ top: 20, right: 30, bottom: 20, left: 10 }}>
              <CartesianGrid stroke={gridStroke} strokeDasharray="3 3" />
              <XAxis
                type="number"
                dataKey="betweenness"
                name="Betweenness Centrality"
                tickFormatter={v => `${(v * 100).toFixed(0)}%`}
                tick={axisTickStyle}
                stroke={theme.borderLight}
                label={{ value: "Betweenness Centrality", position: "insideBottom", offset: -10, style: { fontSize: 12, fill: theme.textSecondary } }}
              />
              <YAxis
                type="number"
                dataKey="inheritance_rate"
                name="Inheritance Rate"
                tickFormatter={v => `${(v * 100).toFixed(0)}%`}
                tick={axisTickStyle}
                stroke={theme.borderLight}
                label={{ value: "Inheritance Rate", angle: -90, position: "insideLeft", style: { fontSize: 12, fill: theme.textSecondary, textAnchor: "middle" } }}
              />
              <ZAxis range={[40, 40]} />
              <Tooltip content={<CustomTooltip theme={theme} />} cursor={{ stroke: theme.textFaint, strokeDasharray: "3 3" }} />
              <Legend
                verticalAlign="top"
                align="right"
                height={30}
                wrapperStyle={{ fontSize: "11px", fontFamily: "'Inter', system-ui, sans-serif", color: theme.textSecondary }}
              />

              <Scatter
                name="All other airports"
                data={baseline}
                fill={theme.textFaint}
                shape={props => <circle cx={props.cx} cy={props.cy} r={3} fill={props.fill} fillOpacity={0.55} />}
              />

              <Scatter name="Florida / leisure cluster" data={flPoints} fill={flColor}>
                <LabelList dataKey="Origin" content={makeFlClusterLabel(flColor)} />
              </Scatter>

              <Scatter name="ATL" data={atlScatterData} fill={atlColor}>
                <LabelList
                  dataKey="Origin"
                  position="top"
                  style={{ fontSize: 11, fontWeight: 700, fontFamily: "'JetBrains Mono', monospace", fill: atlColor }}
                />
              </Scatter>
            </ScatterChart>
          </ResponsiveContainer>
          )}

          <div style={{ fontSize: "12.5px", color: theme.textSecondary, lineHeight: 1.6, marginTop: "12px", maxWidth: "820px" }}>
            <strong style={{ color: theme.textPrimary }}>ATL</strong> (Atlanta Airport) has
            {atlCentrality != null ? ` approximately ${(atlCentrality * 100).toFixed(0)}% betweenness centrality` : " very high betweenness centrality"},
            the most structurally critical airport in the network, which reflects Delta
            adopting it as their main hub. Its inheritance rate is only low-to-mid for a
            node that size, which means it's well buffered. The <strong style={{ color: theme.textPrimary }}>Florida/leisure cluster</strong> (PBI,
            FLL, MIA, TPA, MCO) is the opposite: near-zero centrality, some of the highest
            inheritance rates in the network.
            <br /><br />
            Centrality measures structural position and the inheritance rate measures the
            realized cascade delay risk.
            <br /><br />
            <span style={{ color: theme.textMuted, fontSize: "11.5px" }}>
              {zeroBtwCount} of {airports.length} airports register exactly zero centrality
              (the dense cluster at the left edge): spoke airports with no through-traffic
              never sit on the shortest path between two other airports. ATL's outlier value
              still compresses the rest of the network toward the left edge on this linear axis.{" "}
              {hideATL
                ? "ATL is currently hidden to show the rest more clearly."
                : "Use “Hide ATL outlier” above to zoom in on them."}
            </span>
          </div>
        </div>

        {/* Supporting cards */}
        <div style={{ display: "flex", gap: "20px", alignItems: "stretch" }}>
          <div style={{ ...cardStyle, flex: 1, padding: "18px 22px" }}>
            <div style={eyebrowStyle}>Live &middot; /airports</div>
            <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "32px", fontWeight: 700, color: theme.accentColor, lineHeight: 1, marginBottom: "8px" }}>
              {inheritedPct != null ? `${inheritedPct}%` : "—"}
            </div>
            <div style={{ fontSize: "12px", color: theme.textSecondary, lineHeight: 1.5 }}>
              of Delta's 2025 delay minutes were inherited from a prior late flight,
              network-wide.
            </div>
          </div>

          <div style={{ ...cardStyle, flex: 1, padding: "18px 22px" }}>
            <div style={eyebrowStyle}>Live &middot; /airports</div>
            <div style={{ display: "flex", gap: "18px", marginBottom: "8px" }}>
              <div>
                <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "22px", fontWeight: 700 }}>
                  {cascadeStats ? cascadeStats.total.toLocaleString() : "—"}
                </div>
                <div style={{ fontSize: "10px", color: theme.textMuted }}>cascades found</div>
              </div>
              <div>
                <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "22px", fontWeight: 700 }}>
                  {cascadeStats ? cascadeStats.avgDepth : "—"}
                </div>
                <div style={{ fontSize: "10px", color: theme.textMuted }}>avg depth</div>
              </div>
              <div>
                <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "22px", fontWeight: 700 }}>
                  {cascadeStats ? cascadeStats.maxDepth : "—"}
                </div>
                <div style={{ fontSize: "10px", color: theme.textMuted }}>max depth</div>
              </div>
            </div>
            <div style={{ fontSize: "12px", color: theme.textSecondary, lineHeight: 1.5 }}>
              Cascade = a chain of inherited delay traced through one tail's rotation.
            </div>
          </div>

          <div style={{ ...cardStyle, flex: 1, padding: "18px 22px", borderLeft: `3px solid ${flColor}` }}>
            <div style={{ ...eyebrowStyle, color: flColor }}>Florida / Leisure Paradox</div>
            <div style={{ fontSize: "12.5px", color: theme.textSecondary, lineHeight: 1.6 }}>
              PBI, FLL, MIA, TPA and MCO carry heavy traffic but sit near the bottom of
              the network's structural centrality ranking. This is because they're rarely
              a through-path for other routes; that traffic does not register as "important"
              structurally. Yet these airports relay a disproportionate share of the
              inherited delay.
            </div>
          </div>
        </div>

        <div style={{ ...cardStyle, padding: "12px 22px", display: "flex", alignItems: "center", gap: "10px" }}>
          <span style={{ fontSize: "10.5px", fontWeight: 700, color: theme.textMuted, textTransform: "uppercase", letterSpacing: "0.05em", fontFamily: "'JetBrains Mono', monospace", flexShrink: 0 }}>
            Methodology note
          </span>
          <span style={{ fontSize: "11.5px", color: theme.textMuted, lineHeight: 1.5 }}>
            {zeroBtwCount} of {airports.length} airports tie at exactly zero betweenness
            centrality, a structural artifact of hub-and-spoke topology: pure-spoke
            airports never sit on the shortest path between two other airports, so they
            score identically regardless of traffic volume.
          </span>
        </div>

        {/* Disruption recovery findings */}
        <div style={{ marginTop: "8px" }}>
          <div style={eyebrowStyle}>Disruption Recovery Findings</div>
          <div style={{ fontSize: "12px", color: theme.textMuted, marginBottom: "14px" }}>
            Investigation findings, computed {FINDINGS_DATE} &middot; not exposed by a live endpoint
          </div>

          <div style={{ display: "flex", gap: "20px" }}>
            <div style={{ ...cardStyle, flex: 1, padding: "16px 20px" }}>
              <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "24px", fontWeight: 700, color: theme.successNumber, marginBottom: "6px" }}>
                98%
              </div>
              <div style={{ fontSize: "12px", color: theme.textSecondary, lineHeight: 1.55 }}>
                Of sampled single-leg cancellations, 98% resolved cleanly &mdash; fully
                absorbed with zero induced delay, or requiring no recovery at all &mdash;
                while only 2% showed a genuine, partial-delay tradeoff. 100 scenarios
                sampled, stratified by hub tier and time of day.
              </div>
            </div>

            <div style={{ ...cardStyle, flex: 1, padding: "16px 20px" }}>
              <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "24px", fontWeight: 700, color: theme.textPrimary, marginBottom: "6px" }}>
                Supply &gt; Coordination
              </div>
              <div style={{ fontSize: "12px", color: theme.textSecondary, lineHeight: 1.55 }}>
                In mass-disruption stress tests, candidate scarcity is the dominant
                constraint, not allocation inefficiency. When many flights from one
                airport cancel at once, the limiter is usually too few available
                aircraft on the ground, not a bad assignment problem.
              </div>
            </div>

            <div style={{ ...cardStyle, flex: 1, padding: "16px 20px" }}>
              <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "24px", fontWeight: 700, color: theme.accentColor, marginBottom: "6px" }}>
                13.2%
              </div>
              <div style={{ fontSize: "12px", color: theme.textSecondary, lineHeight: 1.55 }}>
                In a real 4-orphaned-leg whole-aircraft-down scenario (tail N101DQ), the
                optimal DP solver found a recovery plan 13.2% cheaper than the greedy
                baseline by considering the full assignment space instead of committing
                segment-by-segment.
              </div>
            </div>
          </div>
        </div>

      </div>
    </div>
  )
}

export default Analysis
