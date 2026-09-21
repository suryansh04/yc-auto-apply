// The first screen: nothing in this app works until Gmail is connected,
// so nothing in this app is shown until it is.
//
// Connecting used to mean leaving the app, running
// `python -m app.cli gmail-auth` in a terminal and coming back. Now the
// button sends the browser to Google's consent screen and the API's own
// callback brings it back with `?gmail=<outcome>`, which is what the
// messages below explain.
import { useConnectGmail } from '../api/hooks'

// The outcomes `GET /api/gmail/callback` can redirect back with. Each
// one says what happened and what to do next - an error that leaves the
// operator guessing is worse than no error at all.
const OUTCOMES = {
  cancelled: {
    tone: 'warn',
    text: 'You closed Google’s consent screen before finishing. Nothing changed.',
  },
  expired: {
    tone: 'warn',
    text: 'That consent link had expired. Start again and it will work.',
  },
  'no-refresh-token': {
    tone: 'danger',
    text:
      'Google did not issue a new token because this account has already granted ' +
      'access. Remove “YC Auto Apply” at myaccount.google.com/permissions, then connect again.',
  },
  'missing-scope': {
    tone: 'danger',
    text:
      'One of the two permissions was left unticked on Google’s screen. Both are ' +
      'needed: sending, and reading mail to find replies. Connect again and allow both.',
  },
  failed: {
    tone: 'danger',
    text:
      'Google refused the connection. The reason is in the API’s terminal output; ' +
      'check the client secrets file, then try again.',
  },
}

export default function ConnectGmail({ status, outcome }) {
  const connect = useConnectGmail()
  const message = OUTCOMES[outcome]
  const reconnecting = status?.connected && !status?.can_read_replies

  if (!status?.configured) {
    return (
      <div className="connect">
        <h1>Gmail is not set up yet</h1>
        <p className="screen-note">
          These have to be in <code>backend/.env</code> before the app can send anything.
          Add them, then restart the API.
        </p>
        <ul className="connect-missing">
          {(status?.missing_config ?? []).map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </div>
    )
  }

  return (
    <div className="connect">
      <h1>{reconnecting ? 'Reconnect Gmail' : 'Connect Gmail'}</h1>
      <p className="screen-note">
        {reconnecting
          ? 'This account can send mail but cannot read replies, because it was ' +
            'authorised before reply polling existed. Connecting again fixes it.'
          : 'This app sends letters from your Gmail account and reads the replies. ' +
            'Google will ask you to allow both before anything can be sent.'}
      </p>

      {message && (
        <p className={`mark mark-${message.tone}`} role="alert">
          {message.text}
        </p>
      )}

      <dl className="connect-details">
        <dt>Account</dt>
        <dd>{status.account}</dd>
        <dt>Google will be asked for</dt>
        <dd>Sending mail as you, and reading your mail to find replies</dd>
      </dl>

      <button
        type="button"
        className="primary connect-button"
        disabled={connect.isPending}
        onClick={() => connect.mutate()}
      >
        {connect.isPending ? 'Opening Google…' : 'Connect Gmail'}
      </button>

      {connect.isError && (
        <p className="field-error" role="alert">
          Could not start: {connect.error.message}
        </p>
      )}

      <p className="connect-footnote">
        Nothing is sent without your approval, whatever Google allows here.
      </p>
    </div>
  )
}
