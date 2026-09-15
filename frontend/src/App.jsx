import { Link, Route, Routes } from 'react-router-dom'

// Router shell only - Tasks 15-17 fill these four routes in. Names match
// the four backend surfaces they'll each consume:
//   Dashboard    -> GET /api/stats                         (Task 15)
//   Review Queue -> GET /api/queue, approve/edit/reject/redraft (Task 15)
//   Manual Email -> GET /api/manual, POST /api/outreach/{id}/email (Task 16)
//   Contacts     -> GET /api/contacts, GET /api/outreach/{id}/thread (Task 17)

function Placeholder({ name }) {
  return <p>{name} - not built yet (Tasks 15-17).</p>
}

function App() {
  return (
    <>
      <nav>
        <Link to="/">Dashboard</Link>
        <Link to="/queue">Review Queue</Link>
        <Link to="/manual">Manual Email</Link>
        <Link to="/contacts">Contacts</Link>
      </nav>
      <Routes>
        <Route path="/" element={<Placeholder name="Dashboard" />} />
        <Route path="/queue" element={<Placeholder name="Review Queue" />} />
        <Route path="/manual" element={<Placeholder name="Manual Email" />} />
        <Route path="/contacts" element={<Placeholder name="Contacts" />} />
      </Routes>
    </>
  )
}

export default App
