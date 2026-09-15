import { Link, Route, Routes } from 'react-router-dom'
import Pipeline from './screens/Pipeline'

// Router shell. Task 15 fills in Pipeline; the rest are still placeholders
// for Tasks 16-17. Spec §16's four screens plus the manual-email screen
// Task 17 adds:
//   Pipeline     -> GET /api/stats, /api/jobs, POST /api/jobs/{kind} (Task 15, done)
//   Review Queue -> GET /api/queue, approve/edit/reject/redraft (Task 15)
//   Contacts     -> GET /api/contacts                            (Task 17)
//   Thread       -> GET /api/outreach/{id}/thread, POST /api/messages/{id}/label (Task 17)
//   Manual email -> GET /api/manual, POST /api/outreach/{id}/email (Task 16)

function Placeholder({ name }) {
  return <p>{name} - not built yet (Tasks 15-17).</p>
}

function App() {
  return (
    <>
      <nav>
        <Link to="/">Pipeline</Link>
        <Link to="/queue">Review Queue</Link>
        <Link to="/contacts">Contacts</Link>
        <Link to="/manual">Manual email</Link>
      </nav>
      <Routes>
        <Route path="/" element={<Pipeline />} />
        <Route path="/queue" element={<Placeholder name="Review Queue" />} />
        <Route path="/contacts" element={<Placeholder name="Contacts" />} />
        <Route path="/thread/:id" element={<Placeholder name="Thread" />} />
        <Route path="/manual" element={<Placeholder name="Manual email" />} />
      </Routes>
    </>
  )
}

export default App
