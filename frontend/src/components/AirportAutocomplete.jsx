import { useState } from "react"
import { useTheme } from "../ThemeContext"

// Abbreviation -> full name, so typing either "TX" or "Texas" finds Texas airports.
const STATE_NAMES = {
  AL: "Alabama", AK: "Alaska", AZ: "Arizona", AR: "Arkansas", CA: "California",
  CO: "Colorado", CT: "Connecticut", DE: "Delaware", DC: "District of Columbia",
  FL: "Florida", GA: "Georgia", HI: "Hawaii", ID: "Idaho", IL: "Illinois",
  IN: "Indiana", IA: "Iowa", KS: "Kansas", KY: "Kentucky", LA: "Louisiana",
  ME: "Maine", MD: "Maryland", MA: "Massachusetts", MI: "Michigan", MN: "Minnesota",
  MS: "Mississippi", MO: "Missouri", MT: "Montana", NE: "Nebraska", NV: "Nevada",
  NH: "New Hampshire", NJ: "New Jersey", NM: "New Mexico", NY: "New York",
  NC: "North Carolina", ND: "North Dakota", OH: "Ohio", OK: "Oklahoma", OR: "Oregon",
  PA: "Pennsylvania", RI: "Rhode Island", SC: "South Carolina", SD: "South Dakota",
  TN: "Tennessee", TX: "Texas", UT: "Utah", VT: "Vermont", VA: "Virginia",
  WA: "Washington", WV: "West Virginia", WI: "Wisconsin", WY: "Wyoming",
  PR: "Puerto Rico", VI: "Virgin Islands",
}

function matchesQuery(airport, query) {
  const q = query.trim().toUpperCase()
  if (!q) return false
  if (airport.Origin?.toUpperCase().startsWith(q)) return true
  if (airport.city?.toUpperCase().includes(q)) return true
  if (airport.state?.toUpperCase() === q) return true
  const stateName = STATE_NAMES[airport.state]
  if (stateName && stateName.toUpperCase().includes(q)) return true
  return false
}

function AirportAutocomplete({ value, onChangeText, onSelect, airports, placeholder, style }) {
  const { theme } = useTheme()
  const [focused, setFocused] = useState(false)

  const suggestions = focused && value.trim()
    ? airports
        .filter(a => matchesQuery(a, value))
        .sort((a, b) => a.Origin.localeCompare(b.Origin))
        .slice(0, 8)
    : []

  return (
    <div style={{ position: "relative", ...style }}>
      <input
        autoComplete="off"
        placeholder={placeholder}
        value={value}
        onChange={e => onChangeText(e.target.value)}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        onKeyDown={e => { if (e.key === "Escape") e.target.blur() }}
        style={{
          width: "100%", padding: "8px 10px", fontSize: "12.5px",
          fontFamily: "'JetBrains Mono', monospace", border: `1px solid ${theme.border}`,
          borderRadius: "8px", color: theme.textPrimary, background: theme.cardBg,
          boxSizing: "border-box",
        }}
      />
      {suggestions.length > 0 && (
        <div style={{
          position: "absolute", top: "calc(100% + 4px)", left: 0, right: 0, zIndex: 20,
          border: `1px solid ${theme.border}`, borderRadius: "8px", background: theme.cardBg,
          boxShadow: "0 4px 12px rgba(0,0,0,0.15)", overflow: "hidden",
        }}>
          {suggestions.map(a => (
            <div
              key={a.Origin}
              onMouseDown={e => e.preventDefault()}
              onClick={() => onSelect(a.Origin)}
              style={{
                padding: "7px 10px", cursor: "pointer", fontSize: "12px",
                fontFamily: "'Inter', system-ui, sans-serif", color: theme.textPrimary,
              }}
              onMouseEnter={e => { e.currentTarget.style.background = theme.pageBg }}
              onMouseLeave={e => { e.currentTarget.style.background = "transparent" }}
            >
              <span style={{ fontFamily: "'JetBrains Mono', monospace", fontWeight: 700 }}>{a.Origin}</span>
              {a.city && <span style={{ color: theme.textSecondary }}> — {a.city}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default AirportAutocomplete
