import { BrowserRouter, Routes, Route } from "react-router-dom"
import NavBar from "./components/NavBar"
import Overview from "./pages/Overview"
import Live from "./pages/Live"
import Analysis from "./pages/Analysis"
import { ThemeProvider } from "./ThemeContext"

function App() {
  return (
    <ThemeProvider>
      <BrowserRouter>
        <NavBar />
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/replay" element={<Live />} />
          <Route path="/analysis" element={<Analysis />} />
        </Routes>
      </BrowserRouter>
    </ThemeProvider>
  )
}
 
export default App