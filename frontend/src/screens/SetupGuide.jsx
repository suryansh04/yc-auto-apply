// What a first-time operator has to do on Google's side before the
// Connect button can work.
//
// Operator request (2026-09-23): "write the step by step procedure a new
// beginner has to do in order to connect gmail when he first time uses
// this app". Everything the app needs from Google is set up in a console
// this app cannot see, so the instructions have to live where the person
// is stuck - on the screen that is blocking them - rather than in a
// README they would have to know to look for.
//
// Accuracy notes, checked against Google's own docs on 2026-09-23 rather
// than written from memory, because the console's menus were reorganised
// (what older guides call "OAuth consent screen" is now "Google Auth
// platform"):
//
// - Desktop app client, not Web. This app's callback is
//   http://127.0.0.1:8000/api/gmail/callback - a loopback address, which
//   is the "recommended mechanism for obtaining the authorization code"
//   for desktop apps and needs no redirect URL registered. The manual
//   copy/paste (OOB) alternative is "no longer supported".
//   developers.google.com/identity/protocols/oauth2/native-app
// - The 7-day expiry is real and is the thing that will bite hardest:
//   "A Google Cloud Platform project with an OAuth consent screen
//   configured for an external user type and a publishing status of
//   'Testing' is issued a refresh token expiring in 7 days, unless the
//   only OAuth scopes requested are a subset of name, email address, and
//   user profile." Gmail scopes are not that subset.
//   developers.google.com/identity/protocols/oauth2
// - Publishing lifts the timer without verification. An unverified app
//   in production shows the unverified-app screen and is "limited to 100
//   new users", which is 99 more than this needs.
//   support.google.com/cloud/answer/7454865
// - A Gmail-scoped refresh token also dies when the account's password
//   changes. Same source as the 7-day rule.
import { ChevronDown, ExternalLink, ListChecks } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { Separator } from '@/components/ui/separator'

function Code({ children }) {
  return (
    <code className="bg-muted rounded px-1 py-0.5 font-mono text-[0.8em] break-all">
      {children}
    </code>
  )
}

/** A console menu path, written the way the console labels it. */
function Path({ children }) {
  return <span className="font-medium">{children}</span>
}

function Step({ n, title, children }) {
  return (
    <li className="grid grid-cols-[1.75rem_minmax(0,1fr)] gap-x-2">
      <span className="text-muted-foreground pt-0.5 text-sm tabular-nums">{n}.</span>
      <div className="grid gap-1">
        <p className="text-sm leading-snug font-medium">{title}</p>
        <div className="text-muted-foreground grid gap-1 text-sm leading-relaxed">
          {children}
        </div>
      </div>
    </li>
  )
}

function Section({ title, children }) {
  return (
    <div className="grid gap-3">
      <h3 className="text-sm font-semibold tracking-tight">{title}</h3>
      <ol className="grid gap-3">{children}</ol>
    </div>
  )
}

export default function SetupGuide({ defaultOpen = false }) {
  return (
    // Operator request (2026-09-23): make this stand out. It sat in the
    // same plain box as the two read-only facts under it, so the one
    // thing a first-time operator has to ACT on looked like a caption.
    // Tinted panel, an icon, and a ring, so it reads as the way in and
    // not as another row of the card.
    <Collapsible
      defaultOpen={defaultOpen}
      className="group/collapsible border-primary/25 bg-primary/5 ring-primary/10 rounded-lg border ring-1"
    >
      <CollapsibleTrigger asChild>
        <Button
          variant="ghost"
          className="hover:bg-primary/10 h-auto w-full justify-between gap-3 px-4 py-3 text-left whitespace-normal"
        >
          <span className="flex items-start gap-3">
            <span className="bg-primary/10 text-primary mt-0.5 grid size-8 shrink-0 place-items-center rounded-md">
              <ListChecks className="size-4" />
            </span>
            <span className="grid gap-0.5">
              <span className="text-sm font-semibold">
                First time? Set up the Google side
              </span>
              <span className="text-muted-foreground text-xs font-normal">
                About 10 minutes, once. Google has to be told this app exists before it
                will let it touch your mail.
              </span>
            </span>
          </span>
          <ChevronDown className="size-4 shrink-0 opacity-60 transition-transform group-data-[state=open]/collapsible:rotate-180" />
        </Button>
      </CollapsibleTrigger>

      <CollapsibleContent>
        <Separator />
        {/* The steps themselves sit on the plain surface: the tint marks
            the way in, it should not follow you through 14 steps. */}
        <div className="bg-background grid gap-6 rounded-b-lg p-4">
          <Section title="1. Make a project and turn the Gmail API on">
            <Step n={1} title="Open the Google Cloud console">
              <p>
                Go to{' '}
                <a
                  className="underline underline-offset-4"
                  href="https://console.cloud.google.com/"
                  target="_blank"
                  rel="noreferrer"
                >
                  console.cloud.google.com
                </a>{' '}
                and sign in <strong>with the same Gmail account the letters will come
                from</strong>. It is free; a card is not needed for any of this.
              </p>
            </Step>
            <Step n={2} title="Create a project">
              <p>
                Project picker in the top bar → <Path>New project</Path> → name it
                anything (<Code>YC Auto Apply</Code>) → <Path>Create</Path>. Wait for the
                notification, then make sure the picker shows your new project and not
                someone else&rsquo;s.
              </p>
            </Step>
            <Step n={3} title="Enable the Gmail API">
              <p>
                <Path>Menu → APIs &amp; Services → Library</Path>, search{' '}
                <Code>Gmail API</Code>, open it, press <Path>Enable</Path>. Nothing later
                works without this, and the error it causes if you skip it does not
                mention it.
              </p>
            </Step>
          </Section>

          <Section title="2. Fill in the consent screen">
            <Step n={4} title="Start the Google Auth Platform">
              <p>
                <Path>Menu → Google Auth Platform</Path> → <Path>Get started</Path>. This
                is the same thing older guides call the &ldquo;OAuth consent screen&rdquo;
                &mdash; Google renamed it, so screenshots elsewhere will look different.
              </p>
            </Step>
            <Step n={5} title="Answer the four questions">
              <p>
                <Path>App name</Path>: anything you will recognise on a permission screen.{' '}
                <Path>User support email</Path>: your own. <Path>Audience</Path>:{' '}
                <strong>External</strong> &mdash; Internal only exists for Google Workspace
                organisations. <Path>Contact information</Path>: your own again. Agree, then{' '}
                <Path>Create</Path>.
              </p>
            </Step>
            <Step n={6} title="Add yourself as a test user">
              <p>
                <Path>Audience → Test users → Add users</Path> → your own Gmail address →{' '}
                <Path>Save</Path>. Until the app is published, Google refuses consent to
                anyone not on this list &mdash; including you.
              </p>
            </Step>
          </Section>

          <Section title="3. Create the client and download its file">
            <Step n={7} title="Create a Desktop app client">
              <p>
                <Path>Clients → Create client</Path> →{' '}
                <Path>Application type: Desktop app</Path> → name it →{' '}
                <Path>Create</Path>.
              </p>
              <p>
                <strong>Desktop app, not Web app.</strong> This app catches Google&rsquo;s
                redirect at <Code>http://127.0.0.1:8000/api/gmail/callback</Code>, and a
                Desktop client is allowed to use loopback addresses like that without you
                registering any redirect URL. A Web client would reject it until the exact
                URL was added by hand.
              </p>
            </Step>
            <Step n={8} title="Download the JSON">
              <p>
                Press <Path>Download JSON</Path> and keep the file somewhere you can find
                again. Do not email it or commit it &mdash; it is the app&rsquo;s identity
                with Google. (<Code>client_secret*.json</Code> is already git-ignored in
                this repo, so dropping it in the project folder is safe.)
              </p>
            </Step>
          </Section>

          <Section title="4. Tell this app about it">
            <Step n={9} title="Fill in backend/.env">
              <p>Four lines, then save:</p>
              <pre className="bg-muted overflow-x-auto rounded-md p-3 font-mono text-xs leading-relaxed">
{`GMAIL_SENDER_ADDRESS=you@gmail.com
GMAIL_CLIENT_SECRETS_PATH=C:/path/to/client_secret_....json
SIGNATURE=Your Name
RESUME_PATH=C:/path/to/your_resume.pdf`}
              </pre>
              <p>
                Forward slashes work on Windows too. The address must be the account you
                signed into Google with in step 1.
              </p>
            </Step>
            <Step n={10} title="Restart the API">
              <p>
                The <Code>.env</Code> file is read once at start-up, so a running API will
                not notice the change. Stop it and run{' '}
                <Code>python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000</Code>{' '}
                again, then reload this page.
              </p>
            </Step>
          </Section>

          <Section title="5. Connect">
            <Step n={11} title="Press Connect Gmail and get past the warning">
              <p>
                Choose your account. Google will say{' '}
                <em>&ldquo;Google hasn&rsquo;t verified this app&rdquo;</em> &mdash; that is
                expected, because the app is yours and unverified. Press{' '}
                <Path>Advanced</Path> → <Path>Go to &lt;your app name&gt; (unsafe)</Path>.
              </p>
            </Step>
            <Step n={12} title="Tick both boxes">
              <p>
                Google asks for two permissions separately: sending mail as you, and
                reading your mail to find replies. <strong>Both must be ticked.</strong> If
                only one is, this app refuses the token and sends you back here rather than
                half-working.
              </p>
            </Step>
          </Section>

          <Section title="6. Before you rely on it">
            <Step n={13} title="Publish the app, or reconnect every week">
              <p>
                While the publishing status is <Path>Testing</Path>, Google expires the
                token after <strong>7 days</strong> and sending stops. To stop that:{' '}
                <Path>Google Auth Platform → Audience → Publish app</Path>.
              </p>
              <p>
                The app stays unverified &mdash; you will still see the warning screen in
                step 11, and it is capped at 100 users, which is 99 more than you need.
                What changes is that the token stops dying on a timer.
              </p>
            </Step>
            <Step n={14} title="Know the other two things that kill a token">
              <p>
                Changing your Google password revokes tokens that carry Gmail permissions,
                and so does removing this app at{' '}
                <a
                  className="underline underline-offset-4"
                  href="https://myaccount.google.com/permissions"
                  target="_blank"
                  rel="noreferrer"
                >
                  myaccount.google.com/permissions
                </a>
                . In both cases sending fails and you come back to this screen; connecting
                again is the whole fix.
              </p>
            </Step>
          </Section>

          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="secondary">Checked 23 Sep 2026</Badge>
            <Button asChild variant="ghost" size="sm">
              <a
                href="https://developers.google.com/identity/protocols/oauth2"
                target="_blank"
                rel="noreferrer"
              >
                Google&rsquo;s own OAuth docs <ExternalLink />
              </a>
            </Button>
          </div>
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}
