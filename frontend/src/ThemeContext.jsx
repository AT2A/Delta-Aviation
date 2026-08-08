import { createContext, useContext, useEffect, useMemo, useState } from "react"

const STORAGE_KEY = "aviation-theme"

const ACCENT_COLOR = "#2f5fd6"

export const LIGHT_THEME = {
  accentColor: ACCENT_COLOR,
  pageBg: "#f5f6f7",
  cardBg: "#ffffff",
  navBg: "#ffffff",
  border: "#e2e5e9",
  borderLight: "#eef0f2",
  rowBorder: "#f5f6f7",
  textPrimary: "#14181f",
  textSecondary: "#5b6472",
  textMuted: "#8b93a0",
  textFaint: "#c9cdd4",
  pillTrackBg: "#f0f1f3",
  pillActiveBg: "#14181f",
  pillActiveText: "#ffffff",
  modalOverlay: "rgba(20, 24, 31, 0.45)",
  successBg: "#eaf6ef",
  successBorder: "#c7e8d3",
  successSubtext: "#3d5c49",
  successNumber: "#2f8f5b",
  dangerBg: "#fdecea",
  dangerBorder: "#f3b8b0",
  dangerText: "#c0392b",
  mapStyleUrl: "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
}

export const DARK_THEME = {
  accentColor: ACCENT_COLOR,
  pageBg: "#0d1014",
  cardBg: "#1a1e26",
  navBg: "#1a1e26",
  border: "#2a2f38",
  borderLight: "#242a33",
  rowBorder: "#20252d",
  textPrimary: "#e7e9ec",
  textSecondary: "#9aa3b2",
  textMuted: "#6b7684",
  textFaint: "#3a4048",
  pillTrackBg: "#12151b",
  pillActiveBg: "#e7e9ec",
  pillActiveText: "#0d1014",
  modalOverlay: "rgba(0, 0, 0, 0.6)",
  successBg: "#16241c",
  successBorder: "#2c4a37",
  successSubtext: "#8fd4ab",
  successNumber: "#3fae72",
  dangerBg: "#2a1815",
  dangerBorder: "#4a2a24",
  dangerText: "#e0685c",
  mapStyleUrl: "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
}

const ThemeContext = createContext(null)

function getInitialMode() {
  if (typeof window === "undefined") return "light"
  const stored = window.localStorage.getItem(STORAGE_KEY)
  return stored === "dark" || stored === "light" ? stored : "light"
}

export function ThemeProvider({ children }) {
  const [mode, setMode] = useState(getInitialMode)

  useEffect(() => {
    window.localStorage.setItem(STORAGE_KEY, mode)
  }, [mode])

  const value = useMemo(() => ({
    mode,
    theme: mode === "dark" ? DARK_THEME : LIGHT_THEME,
    toggleTheme: () => setMode(m => (m === "dark" ? "light" : "dark")),
  }), [mode])

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme() {
  return useContext(ThemeContext)
}
