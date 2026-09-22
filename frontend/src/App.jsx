import { useState } from 'react'
import { NavLink, Route, Routes, useLocation, useSearchParams } from 'react-router-dom'
import {
  ChevronsUpDown,
  CircleDot,
  Inbox,
  LogOut,
  LayoutDashboard,
  MailQuestion,
  FileUser,
  ListChecks,
  MailCheck,
  SendHorizontal,
  Users,
} from 'lucide-react'
import Pipeline from './screens/Pipeline'
import ReviewQueue from './screens/ReviewQueue'
import Contacts from './screens/Contacts'
import Thread from './screens/Thread'
import ManualEmail from './screens/ManualEmail'
import Approved from './screens/Approved'
import Resume from './screens/Resume'
import Applications from './screens/Applications'
import Sent from './screens/Sent'
import ConnectGmail from './screens/ConnectGmail'
import { useDisconnectGmail, useGmailStatus } from './api/hooks'
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarRail,
  SidebarTrigger,
} from '@/components/ui/sidebar'
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbList,
  BreadcrumbPage,
} from '@/components/ui/breadcrumb'
import { Separator } from '@/components/ui/separator'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Spinner } from '@/components/ui/spinner'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

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

const NAV = [
  { to: '/', end: true, label: 'Pipeline', icon: LayoutDashboard },
  { to: '/queue', label: 'Review', icon: Inbox },
  { to: '/approved', label: 'Approved', icon: SendHorizontal },
  { to: '/sent', label: 'Sent', icon: MailCheck },
  { to: '/applications', label: 'Applications', icon: ListChecks },
  { to: '/contacts', label: 'Founders', icon: Users },
  { to: '/manual', label: 'Missing addresses', icon: MailQuestion },
  { to: '/resume', label: 'My resume', icon: FileUser },
]

// The heading shown in the bar beside the sidebar trigger. Derived from
// the route rather than passed down, so a screen never has to know it is
// being framed by a shell.
const TITLES = {
  '/': 'Pipeline',
  '/queue': 'Review queue',
  '/approved': 'Approved',
  '/sent': 'Sent',
  '/applications': 'Applications',
  '/contacts': 'Founders',
  '/manual': 'Missing addresses',
  '/resume': 'My resume',
}

function currentTitle(pathname) {
  if (TITLES[pathname]) return TITLES[pathname]
  if (pathname.startsWith('/thread/')) return 'Conversation'
  return 'Outreach desk'
}

function App() {
  const gmail = useGmailStatus()
  const disconnect = useDisconnectGmail()
  // Confirmed, not one click: disconnecting costs a trip through
  // Google's consent screen to undo, and the account line is a place the
  // operator's cursor passes on the way to the nav.
  const [confirmLogout, setConfirmLogout] = useState(false)
  const [params] = useSearchParams()
  const location = useLocation()
  const outcome = params.get('gmail')
  const ready = Boolean(gmail.data?.connected && gmail.data?.can_read_replies)

  return (
    <TooltipProvider delayDuration={300}>
      <SidebarProvider>
        <Sidebar collapsible="icon">
          <SidebarHeader>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton size="lg" asChild>
                  <NavLink to="/">
                    <div className="bg-sidebar-primary text-sidebar-primary-foreground flex aspect-square size-8 items-center justify-center rounded-lg">
                      <CircleDot className="size-4" />
                    </div>
                    <div className="grid flex-1 text-left text-sm leading-tight">
                      <span className="truncate font-medium">Outreach desk</span>
                      <span className="text-muted-foreground truncate text-xs">
                        YC founders, one letter at a time
                      </span>
                    </div>
                  </NavLink>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarHeader>

          <SidebarContent>
            {/* Navigation only exists once mail can actually be sent -
                until then the connect screen is the whole app. */}
            {ready && (
              <SidebarGroup>
                <SidebarGroupLabel>Desk</SidebarGroupLabel>
                <SidebarGroupContent>
                  <SidebarMenu>
                    {NAV.map(({ to, end, label, icon: Icon }) => (
                      <SidebarMenuItem key={to}>
                        <SidebarMenuButton asChild tooltip={label}>
                          <NavLink to={to} end={end}>
                            <Icon />
                            <span>{label}</span>
                          </NavLink>
                        </SidebarMenuButton>
                      </SidebarMenuItem>
                    ))}
                  </SidebarMenu>
                </SidebarGroupContent>
              </SidebarGroup>
            )}
          </SidebarContent>

          {/* Which mailbox these letters leave from. Worth saying on
              every screen: it is the one setting that decides what a
              founder sees in their From line, and it is easy to forget
              which account was authorised months ago. */}
          {ready && (
            <SidebarFooter>
              <SidebarMenu>
                <SidebarMenuItem>
                  {/* Operator request (2026-09-23): "give option here for
                      logout too". The account line was the only place in
                      the app that named the connected account, and the
                      only way out of it was to stop using the app. */}
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <SidebarMenuButton size="lg">
                        <span
                          aria-hidden="true"
                          className="flex size-8 shrink-0 items-center justify-center"
                        >
                          <span className="size-2 rounded-full bg-emerald-500" />
                        </span>
                        <span className="grid flex-1 text-left leading-tight">
                          <span className="text-muted-foreground text-xs">Sending as</span>
                          <span className="truncate text-sm">{gmail.data.account}</span>
                        </span>
                        <ChevronsUpDown className="ml-auto size-4 opacity-60" />
                      </SidebarMenuButton>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent side="right" align="end" className="w-64">
                      <DropdownMenuLabel className="text-muted-foreground text-xs font-normal">
                        Letters are sent from this account
                      </DropdownMenuLabel>
                      <DropdownMenuItem variant="destructive" onSelect={() => setConfirmLogout(true)}>
                        <LogOut />
                        Disconnect Gmail
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </SidebarMenuItem>
              </SidebarMenu>
            </SidebarFooter>
          )}

          <SidebarRail />
        </Sidebar>

        <SidebarInset>
          {/* Trigger and breadcrumb only. The screen below owns the page
              heading; repeating it here gave every page two headings
              with the same name, which is a worse answer for a screen
              reader than none. */}
          <header className="flex h-16 shrink-0 items-center gap-2 border-b px-4">
            <SidebarTrigger className="-ml-1" />
            <Separator orientation="vertical" className="mr-2 data-[orientation=vertical]:h-4" />
            <Breadcrumb>
              <BreadcrumbList>
                <BreadcrumbItem>
                  <BreadcrumbPage>{currentTitle(location.pathname)}</BreadcrumbPage>
                </BreadcrumbItem>
              </BreadcrumbList>
            </Breadcrumb>
          </header>
          <main className="flex flex-1 flex-col gap-6 p-4 md:p-6">
            <GatedRoutes gmail={gmail} outcome={outcome} />
          </main>
        </SidebarInset>

        <Dialog open={confirmLogout} onOpenChange={setConfirmLogout}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Disconnect {gmail.data?.account}?</DialogTitle>
              <DialogDescription>
                This app forgets the token, so nothing here can send or read mail as that
                account until you connect it again. Your drafts, approvals and everything
                already sent are untouched.
              </DialogDescription>
            </DialogHeader>
            {/* Said plainly rather than implied: "disconnect" here means
                this machine forgets the token, not that Google withdraws
                the grant. Only the operator can do the second, and the
                app already tells him the same thing when a token turns
                out to be dead (`NoRefreshToken`). */}
            <p className="text-muted-foreground text-sm">
              Google still lists this app until you remove it at{' '}
              <a
                className="underline underline-offset-4"
                href="https://myaccount.google.com/permissions"
                target="_blank"
                rel="noreferrer"
              >
                myaccount.google.com/permissions
              </a>
              .
            </p>
            {disconnect.isError && (
              <p className="text-destructive text-sm" role="alert">
                Could not disconnect: {disconnect.error.message}
              </p>
            )}
            <DialogFooter>
              <Button variant="outline" onClick={() => setConfirmLogout(false)}>
                Cancel
              </Button>
              <Button
                variant="destructive"
                disabled={disconnect.isPending}
                onClick={() =>
                  disconnect.mutate(undefined, { onSuccess: () => setConfirmLogout(false) })
                }
              >
                {disconnect.isPending ? <Spinner /> : null}
                Disconnect
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </SidebarProvider>
    </TooltipProvider>
  )
}

function GatedRoutes({ gmail, outcome }) {
  // An unknown answer is not "not connected": showing the connect screen
  // while the first request is still in flight would flash it at an
  // operator who connected months ago.
  if (gmail.isLoading) {
    return (
      <p className="text-muted-foreground flex items-center gap-2 text-sm">
        <Spinner /> Checking Gmail…
      </p>
    )
  }

  if (gmail.isError) {
    return (
      <Alert variant="destructive" className="max-w-2xl">
        <AlertTitle>
          <h1 className="text-base font-medium">The API is not answering</h1>
        </AlertTitle>
        <AlertDescription>
          <p>
            Start it with{' '}
            <code className="bg-muted rounded px-1 py-0.5 font-mono text-xs">
              python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000
            </code>{' '}
            from <code className="bg-muted rounded px-1 py-0.5 font-mono text-xs">backend/</code>,
            then reload this page.
          </p>
        </AlertDescription>
      </Alert>
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
      <Route path="/approved" element={<Approved />} />
      <Route path="/sent" element={<Sent />} />
      <Route path="/applications" element={<Applications />} />
      <Route path="/contacts" element={<Contacts />} />
      <Route path="/thread/:id" element={<Thread />} />
      <Route path="/manual" element={<ManualEmail />} />
      <Route path="/resume" element={<Resume />} />
    </Routes>
  )
}

export default App
