// The first screen: nothing in this app works until Gmail is connected,
// so nothing in this app is shown until it is.
//
// Connecting used to mean leaving the app, running
// `python -m app.cli gmail-auth` in a terminal and coming back. Now the
// button sends the browser to Google's consent screen and the API's own
// callback brings it back with `?gmail=<outcome>`, which is what the
// messages below explain.
import { useConnectGmail } from '../api/hooks'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Item, ItemContent, ItemDescription, ItemTitle } from '@/components/ui/item'
import { Spinner } from '@/components/ui/spinner'
import SetupGuide from './SetupGuide'

// The outcomes `GET /api/gmail/callback` can redirect back with. Each
// one says what happened and what to do next - an error that leaves the
// operator guessing is worse than no error at all.
const OUTCOMES = {
  cancelled: {
    variant: 'default',
    title: 'Nothing changed',
    text: 'You closed Google’s consent screen before finishing.',
  },
  expired: {
    variant: 'default',
    title: 'That consent link had expired',
    text: 'Start again and it will work.',
  },
  'no-refresh-token': {
    variant: 'destructive',
    title: 'Google did not issue a new token',
    text:
      'This account has already granted access. Remove “YC Auto Apply” at ' +
      'myaccount.google.com/permissions, then connect again.',
  },
  'missing-scope': {
    variant: 'destructive',
    title: 'One permission was left unticked',
    text:
      'Both are needed: sending, and reading mail to find replies. Connect again and allow ' +
      'both.',
  },
  failed: {
    variant: 'destructive',
    title: 'Google refused the connection',
    text:
      'The reason is in the API’s terminal output; check the client secrets file, then try ' +
      'again.',
  },
}

export default function ConnectGmail({ status, outcome }) {
  const connect = useConnectGmail()
  const message = OUTCOMES[outcome]
  const reconnecting = status?.connected && !status?.can_read_replies

  if (!status?.configured) {
    return (
      <Card className="max-w-2xl">
        <CardHeader>
          <CardTitle>
            <h1>Gmail is not set up yet</h1>
          </CardTitle>
          <CardDescription>
            These have to be in{' '}
            <code className="bg-muted rounded px-1 py-0.5 font-mono text-xs">backend/.env</code>{' '}
            before the app can send anything. Add them, then restart the API.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          <ul className="grid gap-1">
            {(status?.missing_config ?? []).map((item) => (
              <li key={item} className="font-mono text-sm">
                {item}
              </li>
            ))}
          </ul>
          {/* Open on this branch: nothing is configured, so whoever is
              reading this has not done the Google side either. */}
          <SetupGuide defaultOpen />
        </CardContent>
      </Card>
    )
  }

  return (
    <Card className="max-w-2xl">
      <CardHeader>
        <CardTitle>
          <h1>{reconnecting ? 'Reconnect Gmail' : 'Connect Gmail'}</h1>
        </CardTitle>
        <CardDescription>
          {reconnecting
            ? 'This account can send mail but cannot read replies, because it was ' +
              'authorised before reply polling existed. Connecting again fixes it.'
            : 'This app sends letters from your Gmail account and reads the replies. ' +
              'Google will ask you to allow both before anything can be sent.'}
        </CardDescription>
      </CardHeader>

      <CardContent className="grid gap-4">
        {message && (
          <Alert variant={message.variant} role="alert">
            <AlertTitle>{message.title}</AlertTitle>
            <AlertDescription>{message.text}</AlertDescription>
          </Alert>
        )}

        {/* First, because it is the only thing on this screen that is
            work: on a first connect the Google side has to exist before
            the button can do anything. Closed by default - the settings
            are in place, so whoever is here has probably done it - but
            it sits above the account rather than under the button, where
            it read as a footnote. */}
        <SetupGuide />

        {/* Operator request (2026-09-23): only while the account is
            being set up for the first time. On a reconnect the operator
            already knows which account this is - he authorised it - and
            the heading above has just told him what is wrong with it, so
            repeating the address and the permissions is two boxes
            between him and the one button he came for. */}
        {!status.connected && (
          <>
            <Item variant="outline">
              <ItemContent>
                <ItemDescription>Account</ItemDescription>
                <ItemTitle>{status.account}</ItemTitle>
              </ItemContent>
            </Item>

            <Item variant="outline">
              <ItemContent>
                <ItemDescription>Google will be asked for</ItemDescription>
                <ItemTitle>Sending mail as you, and reading your mail to find replies</ItemTitle>
              </ItemContent>
            </Item>
          </>
        )}

        {connect.isError && (
          <Alert variant="destructive" role="alert">
            <AlertTitle>Could not start</AlertTitle>
            <AlertDescription>{connect.error.message}</AlertDescription>
          </Alert>
        )}
      </CardContent>

      <CardFooter className="flex-col items-start gap-3">
        <Button disabled={connect.isPending} onClick={() => connect.mutate()}>
          {connect.isPending ? (
            <>
              <Spinner /> Opening Google…
            </>
          ) : (
            'Connect Gmail'
          )}
        </Button>
        <p className="text-muted-foreground text-xs">
          Nothing is sent without your approval, whatever Google allows here.
        </p>
      </CardFooter>
    </Card>
  )
}
