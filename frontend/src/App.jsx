import { Link, Route, Routes } from 'react-router-dom'

// Router shell only - Tasks 15-17 fill these routes in. Spec §16's four
// screens plus the manual-email screen Task 17 adds:
//   Pipeline     -> GET /api/stats                              (Task 15)
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
        <Route path="/" element={<Placeholder name="Pipeline" />} />
        <Route path="/queue" element={<Placeholder name="Review Queue" />} />
        <Route path="/contacts" element={<Placeholder name="Contacts" />} />
        <Route path="/thread/:id" element={<Placeholder name="Thread" />} />
        <Route path="/manual" element={<Placeholder name="Manual email" />} />
      </Routes>
    </>
  )
}

export default App
