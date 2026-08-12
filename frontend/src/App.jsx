import { lazy, Suspense } from "react"
import { BrowserRouter, Routes, Route } from "react-router-dom"
import NavBar from "./components/NavBar"
import { ThemeProvider } from "./ThemeContext"

// Route-level code splitting: Overview and Live both pull in MapView (and
// with it the full deck.gl + maplibre-gl stack, ~1MB+ raw JS) but Analysis
// is a pure recharts page that never touches either -- without this, every
// visit to Analysis still downloaded and parsed the whole map stack it
// never uses, since App.jsx previously imported all three pages statically
// into one bundle.
const Overview = lazy(() => import("./pages/Overview"))
const Live = lazy(() => import("./pages/Live"))
const Analysis = lazy(() => import("./pages/Analysis"))

function App() {
  return (
    <ThemeProvider>
      <BrowserRouter>
        <NavBar />
        <Suspense fallback={null}>
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/replay" element={<Live />} />
            <Route path="/analysis" element={<Analysis />} />
          </Routes>
        </Suspense>
      </BrowserRouter>
    </ThemeProvider>
  )
}
 
export default App