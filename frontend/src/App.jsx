import { NavLink, Route, Routes, useSearchParams } from 'react-router-dom'
import Pipeline from './screens/Pipeline'
import ReviewQueue from './screens/ReviewQueue'
import Contacts from './screens/Contacts'
import Thread from './screens/Thread'
import ManualEmail from './screens/ManualEmail'
import ConnectGmail from './screens/ConnectGmail'
import { useGmailStatus } from './api/hooks'

// Router shell. Spec §16's four screens plus the manual-email screen:
//   Pipeline     -> GET /api/stats, /api/jobs, POST /api/jobs/{kind} (Task 15)
//   Review Queue -> GET /api/queue, approve/edit/reject/redraft (Task 16)
//   Contacts     -> GET /api/contacts                            (Task 17)
//   Thread       -> GET /api/outreach/{id}/thread, POST /api/messages/{id}/label (Task 17)
//   Manual email -> GET /api/manual, POST /api/outreach/{id}/email (Task 17)
//
// All of it sits behind Gmail: an app that cannot send is a tour of
// drafts nobody will ever receive, so `GET /api/gmail/status` decides
// whether the operator sees the dashboard or the connect screen. The
// API's OAuth callback returns the browser here with `?gmail=<outcome>`,
// which the connect screen turns into a sentence.
function App() {
  const gmail = useGmailStatus()
  const [params] = useSearchParams()
  const outcome = params.get('gmail')

  return (
    <div className="app-shell">
      <nav className="app-nav" aria-label="Main">
        <span className="app-wordmark">Outreach desk</span>
        {gmail.data?.connected && gmail.data?.can_read_replies && (
          <>
            <NavLink to="/" end>
              Pipeline
            </NavLink>
            <NavLink to="/queue">Review</NavLink>
            <NavLink to="/contacts">Founders</NavLink>
            <NavLink to="/manual">Missing addresses</NavLink>
          </>
        )}
      </nav>
      <main className="app-main">
        <GatedRoutes gmail={gmail} outcome={outcome} />
      </main>
    </div>
  )
}

function GatedRoutes({ gmail, outcome }) {
  // An unknown answer is not "not connected": showing the connect screen
  // while the first request is still in flight would flash it at an
  // operator who connected months ago.
  if (gmail.isLoading) return <p className="muted">Checking Gmail…</p>

  if (gmail.isError) {
    return (
      <div className="connect">
        <h1>The API is not answering</h1>
        <p className="screen-note">
          Start it with <code>python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000</code>{' '}
          from <code>backend/</code>, then reload this page.
        </p>
      </div>
    )
  }

  // A send-only token (authorised before reply polling existed) is not
  // good enough: the poll job would fail on every run.
  if (!gmail.data?.connected || !gmail.data?.can_read_replies) {
    return <ConnectGmail status={gmail.data} outcome={outcome} />
  }

  return (
    <Routes>
      <Route path="/" element={<Pipeline />} />
      <Route path="/queue" element={<ReviewQueue />} />
      <Route path="/contacts" element={<Contacts />} />
      <Route path="/thread/:id" element={<Thread />} />
      <Route path="/manual" element={<ManualEmail />} />
    </Routes>
  )
}

export default App
