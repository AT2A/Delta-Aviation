import { BrowserRouter, Routes, Route } from "react-router-dom"
import NavBar from "./components/NavBar"
import Overview from "./pages/Overview"
import Live from "./pages/Live"
 
function App() {
  return (
    <BrowserRouter>
      <NavBar />
      <Routes>
        <Route path="/" element={<Overview />} />
        <Route path="/replay" element={<Live />} />
      </Routes>
    </BrowserRouter>
  )
}
 
export default App