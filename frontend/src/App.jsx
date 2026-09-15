import { Link, Route, Routes } from 'react-router-dom'
import Pipeline from './screens/Pipeline'
import ReviewQueue from './screens/ReviewQueue'
import Contacts from './screens/Contacts'
import Thread from './screens/Thread'
import ManualEmail from './screens/ManualEmail'

// Router shell. Spec §16's four screens plus the manual-email screen:
//   Pipeline     -> GET /api/stats, /api/jobs, POST /api/jobs/{kind} (Task 15)
//   Review Queue -> GET /api/queue, approve/edit/reject/redraft (Task 16)
//   Contacts     -> GET /api/contacts                            (Task 17)
//   Thread       -> GET /api/outreach/{id}/thread, POST /api/messages/{id}/label (Task 17)
//   Manual email -> GET /api/manual, POST /api/outreach/{id}/email (Task 17)

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
        <Route path="/queue" element={<ReviewQueue />} />
        <Route path="/contacts" element={<Contacts />} />
        <Route path="/thread/:id" element={<Thread />} />
        <Route path="/manual" element={<ManualEmail />} />
      </Routes>
    </>
  )
}

export default App
