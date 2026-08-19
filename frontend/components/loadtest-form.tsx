'use client'

import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  Activity,
  AlertTriangle,
  Check,
  ChevronDown,
  CircleAlert,
  Clock,
  KeyRound,
  Lock,
  Minus,
  Play,
  Plus,
  SlidersHorizontal,
  Trash2,
  TrendingUp,
  X,
  Zap,
} from 'lucide-react'
import {
  getLoadTestScenarios,
  submitLoadTest,
  type AuthConfigWire,
  type LoadTestRequestBody,
  type LoadTestScenarioInfo,
  type LoadTestScenarioType,
} from '@/lib/api'
import { cn } from '@/lib/format'
import { MagneticButton, Panel, Spinner } from './ui'
import { Plate } from './decor'

const SCENARIO_ICONS: Record<string, typeof TrendingUp> = {
  'trending-up': TrendingUp,
  minus: Minus,
  zap: Zap,
  clock: Clock,
  'alert-triangle': AlertTriangle,
}

const HTTP_METHODS = ['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'] as const
const BODY_METHODS = ['POST', 'PUT', 'PATCH']

// Offline fallback only - the live list comes from GET /api/loadtest/scenarios.
// Kept in step with the backend catalogue so a fetch failure doesn't offer a
// scenario the API would reject.
const FALLBACK_SCENARIOS: LoadTestScenarioInfo[] = [
  { id: 'ramp', label: 'Ramp Up/Down', description: 'Gradually increase VUs to the target, hold, then ramp down.', icon_hint: 'trending-up' },
  { id: 'constant', label: 'Constant Load', description: 'Maintain a fixed number of VUs for the entire duration.', icon_hint: 'minus' },
  { id: 'spike', label: 'Spike Test', description: 'Sudden burst from 0 to target VUs. Tests resilience to traffic surges.', icon_hint: 'zap' },
  { id: 'soak', label: 'Soak Test', description: 'Moderate load sustained for an extended period. Reveals leaks and drift.', icon_hint: 'clock' },
  { id: 'stress', label: 'Stress Test', description: 'Progressively increase load beyond expected capacity to find the breaking point.', icon_hint: 'alert-triangle' },
]

// Parity with the backend validator (schemas.py::_validate_target_url): an
// absolute http(s) URL with a host. Private/internal hosts are deliberately NOT
// rejected here - unlike a scan target, load-testing a service on your own
// network is a first-class self-hosted use case, and the backend allows it.
function isValidTargetUrl(v: string): boolean {
  const t = v.trim()
  if (!t) return false
  try {
    const u = new URL(t)
    return (u.protocol === 'http:' || u.protocol === 'https:') && u.hostname.length > 0
  } catch {
    return false
  }
}

type LoginType = 'auto' | 'form' | 'json'

export function LoadTestForm() {
  const router = useRouter()
  const [scenarios, setScenarios] = useState<LoadTestScenarioInfo[]>(FALLBACK_SCENARIOS)
  const [loading, setLoading] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)

  const [targetUrl, setTargetUrl] = useState('')
  const [scenario, setScenario] = useState<LoadTestScenarioType>('ramp')
  const [vus, setVus] = useState(50)
  const [duration, setDuration] = useState(30)
  const [method, setMethod] = useState<string>('GET')
  const [authorized, setAuthorized] = useState(false)

  const [authOpen, setAuthOpen] = useState(false)
  const [loginType, setLoginType] = useState<LoginType>('auto')
  const [loginUrl, setLoginUrl] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [usernameField, setUsernameField] = useState('')
  const [passwordField, setPasswordField] = useState('')
  const [tokenJsonPath, setTokenJsonPath] = useState('')

  const [requestOpen, setRequestOpen] = useState(false)
  const [headers, setHeaders] = useState<{ key: string; value: string }[]>([])
  const [requestBody, setRequestBody] = useState('')
  const [thresholdP95, setThresholdP95] = useState('')
  const [thresholdErrorRate, setThresholdErrorRate] = useState('')
  const [notes, setNotes] = useState('')

  const [checksOpen, setChecksOpen] = useState(false)

  useEffect(() => {
    getLoadTestScenarios()
      .then((s) => {
        if (s.length > 0) setScenarios(s)
      })
      .catch(() => {
        /* keep the fallback catalogue */
      })
  }, [])

  const urlTouched = targetUrl.trim().length > 0
  const urlValid = isValidTargetUrl(targetUrl)
  const authComplete =
    loginUrl.trim() !== '' && username.trim() !== '' && password.trim() !== ''
  const canSubmit = urlValid && authorized && !loading

  const selectedScenario = scenarios.find((s) => s.id === scenario)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!canSubmit) return
    setSubmitError(null)
    setLoading(true)

    try {
      const body: LoadTestRequestBody = {
        target_url: targetUrl.trim(),
        authorized: true,
        scenario,
        virtual_users: vus,
        duration_seconds: duration,
        http_method: method,
        notes: notes.trim() || undefined,
      }

      const validHeaders = headers.filter((h) => h.key.trim() && h.value.trim())
      if (validHeaders.length > 0) {
        body.headers = Object.fromEntries(
          validHeaders.map((h) => [h.key.trim(), h.value.trim()]),
        )
      }

      if (requestBody.trim() && BODY_METHODS.includes(method)) {
        body.request_body = requestBody.trim()
      }

      const thresholds: Record<string, number> = {}
      if (thresholdP95 && !Number.isNaN(Number(thresholdP95))) {
        thresholds.http_req_duration_p95 = Number(thresholdP95)
      }
      if (thresholdErrorRate && !Number.isNaN(Number(thresholdErrorRate))) {
        thresholds.http_req_failed_rate = Number(thresholdErrorRate) / 100
      }
      if (Object.keys(thresholds).length > 0) body.thresholds = thresholds

      if (authComplete) {
        const auth: AuthConfigWire = {
          login_url: loginUrl.trim(),
          username: username.trim(),
          password,
          login_type: loginType,
        }
        if (usernameField.trim()) auth.username_field = usernameField.trim()
        if (passwordField.trim()) auth.password_field = passwordField.trim()
        if (loginType === 'json' && tokenJsonPath.trim())
          auth.token_json_path = tokenJsonPath.trim()
        body.auth = auth
      }

      const res = await submitLoadTest(body)
      router.push(`/loadtest/${res.job_id}/status`)
    } catch (err) {
      setLoading(false)
      setSubmitError(
        err instanceof Error ? err.message : 'Cannot reach the load-test server. Is the backend running?',
      )
    }
  }

  // The instruments this run will actually engage, derived from form state -
  // the same "systems engaged" contract the scan form uses, except a load test
  // has no module API to enumerate, so the list is composed from the config.
  const activeModules = [
    {
      id: 'k6-core',
      label: 'k6 Generator',
      icon: Activity,
      description:
        'The core k6 engine spawns the virtual users, executes the request payload, and records the load metrics.',
    },
    {
      id: 'scenario',
      label: selectedScenario?.label ?? 'Ramp Up/Down',
      icon: SCENARIO_ICONS[selectedScenario?.icon_hint ?? 'trending-up'] ?? TrendingUp,
      description:
        selectedScenario?.description ?? 'Virtual-user scaling strategy and traffic shaping.',
    },
    ...(thresholdP95 || thresholdErrorRate
      ? [
          {
            id: 'thresholds',
            label: 'Threshold Monitor',
            icon: AlertTriangle,
            description:
              'Watches the live run and fails the test if latency or error rate leaves the limits you set.',
          },
        ]
      : []),
    ...(authComplete
      ? [
          {
            id: 'auth',
            label: 'Pre-Flight Auth',
            icon: Lock,
            description:
              'Authenticates against the target and injects the session into every virtual user before load starts.',
          },
        ]
      : []),
  ]

  return (
    <div className="relative w-full overflow-x-clip">
      {/* Measurement-under-load instruments - the page's own subject. Side
          margins only, clear of the 640px form column. */}
      <Plate src="pressure-gauge" rotate={-6} opacity={0.24} delay={0}
        className="left-[2%] top-[18%] hidden h-[400px] w-[400px] xl:block" />
      <Plate src="hourglass" rotate={7} opacity={0.22} delay={3}
        className="right-[3%] top-[38%] hidden h-[360px] w-[360px] -translate-y-1/2 xl:block" />
      <Plate src="telegraph-key" rotate={-4} opacity={0.2} delay={6}
        className="bottom-[8%] left-[5%] hidden h-[320px] w-[320px] xl:block" />

      <div className="mx-auto flex min-h-screen w-full max-w-[640px] flex-col justify-center px-6 py-20">
        <header className="mb-8 flex flex-col items-center text-center onus-fade-up">
          <LoadDial vus={vus} armed={urlValid} />
          <p className="signage mb-2.5 mt-5 text-[10px] text-accent text-glow-cyan">
            Capacity Under Load
          </p>
          <h1 className="signage text-[16px] font-semibold leading-[1.5] tracking-[0.09em] text-ink">
            Find the breaking point<br />before your users do.
          </h1>
          <p className="mx-auto mt-3 max-w-[420px] text-[13.5px] leading-relaxed text-ink-dim">
            k6 drives real virtual users at an endpoint you control. Latency,
            throughput and error rate are measured, not estimated.
          </p>
        </header>

        <Panel
          className="spotlight relative p-6 onus-fade-up"
          style={{
            animationDelay: '60ms',
            boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.05), 0 24px 60px -24px rgba(0,0,0,0.85)',
          }}
        >
          <form onSubmit={handleSubmit} noValidate>
            {/* Target URL */}
            <label htmlFor="target-url" className="mb-2 block text-[12px] font-medium text-ink-dim">
              Target URL
            </label>
            <div className="relative">
              <input
                id="target-url"
                value={targetUrl}
                onChange={(e) => setTargetUrl(e.target.value)}
                placeholder="https://api.example.com/endpoint"
                autoComplete="off"
                spellCheck={false}
                aria-invalid={urlTouched && !urlValid}
                aria-describedby="target-url-hint"
                className={cn(
                  'w-full rounded-md border bg-canvas px-3.5 py-3 pr-10 font-mono text-[14px] text-ink placeholder:text-ink-faint focus:outline-none focus:ring-1',
                  urlTouched && !urlValid
                    ? 'border-crit/60 focus:ring-crit/50'
                    : 'border-line focus:border-accent/60 focus:ring-accent/40',
                )}
              />
              {urlTouched && (
                <span className="absolute right-3 top-1/2 -translate-y-1/2">
                  {urlValid ? (
                    <Check className="h-4 w-4 text-[var(--color-cyan)]" strokeWidth={2} />
                  ) : (
                    <X className="h-4 w-4 text-crit" strokeWidth={2} />
                  )}
                </span>
              )}
            </div>
            <p id="target-url-hint" className="mt-1.5 min-h-[16px] text-[11px] text-ink-faint">
              {urlTouched && !urlValid
                ? 'Enter an absolute URL beginning with http:// or https://.'
                : 'The exact endpoint to drive. Must be reachable from the ONUS server.'}
            </p>

            {/* Scenario */}
            <p className="mt-4 mb-2 text-[12px] font-medium text-ink-dim">Scenario</p>
            <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
              {scenarios.map((s) => {
                const Icon = SCENARIO_ICONS[s.icon_hint] ?? TrendingUp
                const active = scenario === s.id
                return (
                  <button
                    key={s.id}
                    type="button"
                    onClick={() => setScenario(s.id)}
                    aria-pressed={active}
                    className={cn(
                      'flex gap-2.5 rounded-md border px-3.5 py-3 text-left transition-colors',
                      active
                        ? 'border-accent/60 bg-accent/[0.06]'
                        : 'border-line hover:border-line-strong',
                    )}
                  >
                    <Icon
                      className={cn('mt-[1px] h-4 w-4 shrink-0', active ? 'text-accent' : 'text-ink-faint')}
                      strokeWidth={1.7}
                    />
                    <div className="min-w-0">
                      <div className={cn('text-[12px] font-semibold', active ? 'text-accent' : 'text-ink')}>
                        {s.label}
                      </div>
                      <div className="mt-1 text-[10.5px] leading-snug text-ink-faint">
                        {s.description}
                      </div>
                    </div>
                  </button>
                )
              })}
            </div>

            {/* Load shape */}
            <div className="mt-4 grid grid-cols-2 gap-3">
              <div>
                <label htmlFor="vus" className="mb-2 block text-[12px] font-medium text-ink-dim">
                  Virtual users
                </label>
                <input
                  id="vus"
                  type="number"
                  min={1}
                  max={1000}
                  value={vus}
                  onChange={(e) => setVus(Math.max(1, Math.min(1000, Number(e.target.value) || 1)))}
                  className="tnum w-full rounded-md border border-line bg-canvas px-3.5 py-3 font-mono text-[14px] text-ink focus:border-accent/60 focus:outline-none focus:ring-1 focus:ring-accent/40"
                />
                <p className="mt-1.5 text-[11px] text-ink-faint">Concurrent users (1–1000)</p>
              </div>
              <div>
                <label htmlFor="duration" className="mb-2 block text-[12px] font-medium text-ink-dim">
                  Duration
                </label>
                <div className="relative">
                  <input
                    id="duration"
                    type="number"
                    min={5}
                    max={3600}
                    value={duration}
                    onChange={(e) =>
                      setDuration(Math.max(5, Math.min(3600, Number(e.target.value) || 5)))
                    }
                    className="tnum w-full rounded-md border border-line bg-canvas px-3.5 py-3 pr-12 font-mono text-[14px] text-ink focus:border-accent/60 focus:outline-none focus:ring-1 focus:ring-accent/40"
                  />
                  <span className="pointer-events-none absolute right-3.5 top-1/2 -translate-y-1/2 font-mono text-[11px] text-ink-faint">
                    sec
                  </span>
                </div>
                <p className="mt-1.5 text-[11px] text-ink-faint">Test duration (5s–1h)</p>
              </div>
            </div>

            {/* Authorization - the one amber (legal/policy) affordance */}
            <label
              className={cn(
                'mt-4 flex cursor-pointer items-start gap-3 rounded-md border p-3.5 transition-colors',
                authorized
                  ? 'border-authz/50 bg-authz/[0.07]'
                  : 'border-line bg-canvas hover:border-line-strong',
              )}
            >
              <span
                className={cn(
                  'mt-[1px] flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-xs border',
                  authorized ? 'border-authz bg-authz/20' : 'border-line-strong',
                )}
              >
                {authorized && <Check className="h-3 w-3 text-authz" strokeWidth={3} />}
              </span>
              <input
                type="checkbox"
                className="sr-only"
                checked={authorized}
                onChange={(e) => setAuthorized(e.target.checked)}
                aria-label="I confirm I am authorized to load test this target and that it will generate significant traffic"
              />
              <span className="text-[12.5px] leading-relaxed text-ink-dim">
                I confirm I am authorized to load test this target, and that this
                run will generate significant traffic against it.
              </span>
            </label>

            {/* Authenticated test (optional) */}
            <Disclosure
              open={authOpen}
              onToggle={() => setAuthOpen((v) => !v)}
              icon={KeyRound}
              title="Authenticated test"
              className="mt-4"
            >
              <div className="flex rounded-md border border-line bg-panel p-0.5">
                {(['auto', 'form', 'json'] as LoginType[]).map((t) => (
                  <button
                    key={t}
                    type="button"
                    onClick={() => setLoginType(t)}
                    className={cn(
                      'flex-1 rounded-[5px] px-2 py-1.5 text-[12px] font-medium capitalize transition-colors',
                      loginType === t ? 'bg-accent/15 text-accent' : 'text-ink-dim hover:text-ink',
                    )}
                  >
                    {t === 'auto' ? 'Auto-detect' : t === 'json' ? 'JSON API' : 'Form'}
                  </button>
                ))}
              </div>

              <Field label="Login URL" value={loginUrl} onChange={setLoginUrl} placeholder="https://api.example.com/login" mono />
              <div className="grid grid-cols-2 gap-3">
                <Field label="Username" value={username} onChange={setUsername} placeholder="user" />
                <Field label="Password" value={password} onChange={setPassword} placeholder="••••••••" type="password" />
              </div>

              <button
                type="button"
                onClick={() => setAdvancedOpen((v) => !v)}
                className="flex items-center gap-1.5 text-[11px] font-medium text-ink-faint hover:text-ink-dim"
              >
                <ChevronDown className={cn('h-3.5 w-3.5 transition-transform', advancedOpen && 'rotate-180')} strokeWidth={1.6} />
                Advanced - field-name overrides
              </button>
              {advancedOpen && (
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Username field" value={usernameField} onChange={setUsernameField} placeholder="username" mono />
                  <Field label="Password field" value={passwordField} onChange={setPasswordField} placeholder="password" mono />
                </div>
              )}

              {loginType === 'json' && (
                <Field label="Token JSON path" value={tokenJsonPath} onChange={setTokenJsonPath} placeholder="data.token" mono />
              )}

              {authComplete && (
                <div className="flex items-center gap-2 rounded-md border border-accent/30 bg-accent/[0.07] px-3 py-2 text-[11.5px] text-accent-soft">
                  <Lock className="h-3.5 w-3.5" strokeWidth={1.6} />
                  Will authenticate to target before load starts.
                </div>
              )}
            </Disclosure>

            {/* Request shape & thresholds (optional) */}
            <Disclosure
              open={requestOpen}
              onToggle={() => setRequestOpen((v) => !v)}
              icon={SlidersHorizontal}
              title="Request & thresholds"
              className="mt-3"
            >
              <div>
                <p className="mb-1.5 text-[11.5px] font-medium text-ink-dim">HTTP method</p>
                <div className="flex flex-wrap gap-1.5">
                  {HTTP_METHODS.map((m) => (
                    <button
                      key={m}
                      type="button"
                      onClick={() => setMethod(m)}
                      aria-pressed={method === m}
                      className={cn(
                        'rounded-[5px] border px-2.5 py-1 font-mono text-[11px] font-medium transition-colors',
                        method === m
                          ? 'border-accent/60 bg-accent/15 text-accent'
                          : 'border-line text-ink-dim hover:border-line-strong hover:text-ink',
                      )}
                    >
                      {m}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <div className="mb-1.5 flex items-center justify-between">
                  <p className="text-[11.5px] font-medium text-ink-dim">Custom headers</p>
                  <button
                    type="button"
                    onClick={() => setHeaders([...headers, { key: '', value: '' }])}
                    className="flex items-center gap-1 text-[11px] font-medium text-accent hover:text-accent-deep"
                  >
                    <Plus className="h-3 w-3" strokeWidth={2} /> Add
                  </button>
                </div>
                {headers.length === 0 ? (
                  <p className="text-[11px] text-ink-faint">
                    None. Requests are sent with k6&apos;s defaults.
                  </p>
                ) : (
                  <div className="space-y-2">
                    {headers.map((h, i) => (
                      <div key={i} className="flex gap-2">
                        <input
                          value={h.key}
                          onChange={(e) => {
                            const next = [...headers]
                            next[i] = { ...next[i], key: e.target.value }
                            setHeaders(next)
                          }}
                          placeholder="Header name"
                          aria-label={`Header ${i + 1} name`}
                          className="min-w-0 flex-1 rounded-md border border-line bg-panel px-3 py-2 font-mono text-[12px] text-ink placeholder:text-ink-faint focus:border-accent/60 focus:outline-none focus:ring-1 focus:ring-accent/40"
                        />
                        <input
                          value={h.value}
                          onChange={(e) => {
                            const next = [...headers]
                            next[i] = { ...next[i], value: e.target.value }
                            setHeaders(next)
                          }}
                          placeholder="Value"
                          aria-label={`Header ${i + 1} value`}
                          className="min-w-0 flex-1 rounded-md border border-line bg-panel px-3 py-2 font-mono text-[12px] text-ink placeholder:text-ink-faint focus:border-accent/60 focus:outline-none focus:ring-1 focus:ring-accent/40"
                        />
                        <button
                          type="button"
                          onClick={() => setHeaders(headers.filter((_, j) => j !== i))}
                          aria-label={`Remove header ${i + 1}`}
                          className="shrink-0 rounded-md px-2 text-ink-faint transition-colors hover:text-crit"
                        >
                          <Trash2 className="h-3.5 w-3.5" strokeWidth={1.7} />
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {BODY_METHODS.includes(method) && (
                <div>
                  <label htmlFor="request-body" className="mb-1.5 block text-[11.5px] font-medium text-ink-dim">
                    Request body
                  </label>
                  <textarea
                    id="request-body"
                    value={requestBody}
                    onChange={(e) => setRequestBody(e.target.value)}
                    placeholder='{"key": "value"}'
                    rows={3}
                    className="w-full resize-y rounded-md border border-line bg-panel px-3 py-2 font-mono text-[12px] text-ink placeholder:text-ink-faint focus:border-accent/60 focus:outline-none focus:ring-1 focus:ring-accent/40"
                  />
                </div>
              )}

              <div className="grid grid-cols-2 gap-3">
                <Field label="p95 latency budget (ms)" value={thresholdP95} onChange={setThresholdP95} placeholder="500" mono />
                <Field label="Max error rate (%)" value={thresholdErrorRate} onChange={setThresholdErrorRate} placeholder="1" mono />
              </div>
              <p className="-mt-1 text-[11px] text-ink-faint">
                A run that exceeds either budget is reported as failed.
              </p>

              <div>
                <label htmlFor="notes" className="mb-1.5 block text-[11.5px] font-medium text-ink-dim">
                  Notes
                </label>
                <textarea
                  id="notes"
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="e.g., checkout API behind the CDN, pre-Black-Friday baseline…"
                  rows={2}
                  className="w-full resize-y rounded-md border border-line bg-panel px-3 py-2 text-[12.5px] text-ink placeholder:text-ink-faint focus:border-accent/60 focus:outline-none focus:ring-1 focus:ring-accent/40"
                />
              </div>
            </Disclosure>

            {/* Inline submit error - one at a time, above the button */}
            {submitError && (
              <div className="mt-4 flex items-start gap-2 rounded-md border border-crit/40 bg-crit/[0.08] px-3.5 py-3 text-[12.5px] text-crit">
                <CircleAlert className="mt-[1px] h-4 w-4 shrink-0" strokeWidth={1.7} />
                <span>{submitError}</span>
              </div>
            )}

            <MagneticButton
              type="submit"
              disabled={!canSubmit}
              className={cn(
                'signage mt-5 flex w-full items-center justify-center gap-2 rounded-[3px] px-4 py-3.5 text-[12px] font-bold',
                canSubmit
                  ? 'bg-accent text-[#03141a] glow-cyan hover:bg-accent-soft'
                  : 'cursor-not-allowed border border-line bg-raised-2 text-ink-faint',
              )}
            >
              {loading ? (
                <>
                  <Spinner className="h-4 w-4" />
                  Starting run…
                </>
              ) : (
                <>
                  <Play className="h-4 w-4" strokeWidth={1.8} />
                  Begin Load Test
                </>
              )}
            </MagneticButton>
          </form>
        </Panel>

        {/* Systems engaged - composed from the current configuration */}
        <div className="mt-8 onus-fade-up" style={{ animationDelay: '120ms' }}>
          <div className="mb-2.5 flex items-center gap-3">
            <p className="text-[10.5px] uppercase tracking-[0.22em] text-ink-faint">Systems engaged</p>
            <span className="h-px flex-1 bg-line" />
            <span className="tnum font-mono text-[10.5px] text-ink-faint">{activeModules.length}</span>
          </div>
          <Panel className="overflow-hidden">
            <div className="grid grid-cols-2 gap-px bg-line sm:grid-cols-4">
              {activeModules.map((m) => {
                const Icon = m.icon
                return (
                  <div key={m.id} className="flex items-center gap-2.5 bg-panel px-3.5 py-3">
                    <Icon className="h-4 w-4 shrink-0 text-ink-dim" strokeWidth={1.6} />
                    <span className="truncate text-[12px] text-ink-dim">{m.label}</span>
                  </div>
                )
              })}
            </div>
          </Panel>

          <div className="mt-4 overflow-hidden rounded-md border border-line bg-panel">
            <button
              type="button"
              onClick={() => setChecksOpen((v) => !v)}
              className="flex w-full items-center justify-between px-4 py-3 text-left text-[12.5px] font-medium text-ink-dim"
              aria-expanded={checksOpen}
            >
              What does this test measure?
              <ChevronDown className={cn('h-4 w-4 text-ink-faint transition-transform', checksOpen && 'rotate-180')} strokeWidth={1.6} />
            </button>
            {checksOpen && (
              <ul className="divide-y divide-line border-t border-line">
                {activeModules.map((m) => {
                  const Icon = m.icon
                  return (
                    <li key={m.id} className="flex gap-3 px-4 py-3">
                      <Icon className="mt-0.5 h-4 w-4 shrink-0 text-accent/70" strokeWidth={1.6} />
                      <div>
                        <p className="text-[12.5px] font-medium text-ink">{m.label}</p>
                        <p className="mt-0.5 text-[12px] leading-relaxed text-ink-dim">{m.description}</p>
                      </div>
                    </li>
                  )
                })}
              </ul>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

// The hero's instrument anchor, answering the scan form's target reticle: a
// pressure dial whose needle tracks the configured virtual-user count and warms
// to the accent once a valid endpoint is set - the front-panel gesture of a rig
// coming under load. The sweep is logarithmic so the 1–1000 VU range reads
// across the whole dial instead of bunching at the low end.
function LoadDial({ vus, armed }: { vus: number; armed: boolean }) {
  const stroke = armed ? 'var(--color-accent)' : 'var(--color-ink-faint)'
  const frac = Math.min(1, Math.max(0, Math.log10(Math.max(1, vus)) / 3))
  const angle = -120 + frac * 240
  const cx = 38
  const cy = 42
  return (
    <div className="relative h-[76px] w-[76px]" aria-hidden="true">
      <svg viewBox="0 0 76 76" className="onus-breathe h-full w-full">
        {/* dial face */}
        <circle
          cx={cx}
          cy={cy}
          r="25"
          fill="none"
          stroke={stroke}
          strokeWidth="1"
          opacity={armed ? 0.6 : 0.32}
          style={{ transition: 'stroke 0.5s, opacity 0.5s' }}
        />
        {/* calibration ticks across the 240° sweep */}
        {[-120, -60, 0, 60, 120].map((a) => {
          const r = ((a - 90) * Math.PI) / 180
          return (
            <line
              key={a}
              x1={cx + 21 * Math.cos(r)}
              y1={cy + 21 * Math.sin(r)}
              x2={cx + 25 * Math.cos(r)}
              y2={cy + 25 * Math.sin(r)}
              stroke={stroke}
              strokeWidth="1"
              strokeLinecap="round"
              opacity="0.55"
              style={{ transition: 'stroke 0.5s' }}
            />
          )
        })}
        {/* Needle. Rotated as a group rather than by recomputing x2/y2: the
            line's endpoint attributes are not CSS geometry properties, so a
            transition on them would not animate - transform does. */}
        <g
          style={{
            transform: `rotate(${angle}deg)`,
            transformOrigin: `${cx}px ${cy}px`,
            transition: 'transform 0.5s var(--ease-hud)',
          }}
        >
          <line
            x1={cx}
            y1={cy}
            x2={cx}
            y2={cy - 18}
            stroke={armed ? 'var(--color-accent)' : 'var(--color-ink-faint)'}
            strokeWidth="1.6"
            strokeLinecap="round"
            style={{ transition: 'stroke 0.5s' }}
          />
        </g>
        <circle
          cx={cx}
          cy={cy}
          r={armed ? 3 : 1.6}
          fill={armed ? 'var(--color-accent)' : 'var(--color-ink-faint)'}
          style={{ transition: 'r 0.5s, fill 0.5s' }}
        />
      </svg>
    </div>
  )
}

// Collapsible section matching the scan form's "Authenticated scan (optional)"
// affordance: one bordered well on the canvas tint, hairline-separated body.
function Disclosure({
  open,
  onToggle,
  icon: Icon,
  title,
  className,
  children,
}: {
  open: boolean
  onToggle: () => void
  icon: typeof KeyRound
  title: string
  className?: string
  children: React.ReactNode
}) {
  return (
    <div className={cn('overflow-hidden rounded-md border border-line bg-canvas', className)}>
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center justify-between px-3.5 py-3 text-left"
        aria-expanded={open}
      >
        <span className="flex items-center gap-2 text-[12.5px] font-medium text-ink-dim">
          <Icon className="h-4 w-4 text-ink-faint" strokeWidth={1.6} />
          {title}
          <span className="text-ink-faint">(optional)</span>
        </span>
        <ChevronDown
          className={cn('h-4 w-4 text-ink-faint transition-transform', open && 'rotate-180')}
          strokeWidth={1.6}
        />
      </button>
      {open && <div className="space-y-3 border-t border-line px-3.5 py-4">{children}</div>}
    </div>
  )
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  type = 'text',
  mono,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
  type?: string
  mono?: boolean
}) {
  const id = useMemo(() => 'lt-' + label.toLowerCase().replace(/[^a-z0-9]+/g, '-'), [label])
  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-[11.5px] font-medium text-ink-dim">
        {label}
      </label>
      <input
        id={id}
        type={type}
        value={value}
        autoComplete="off"
        spellCheck={false}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className={cn(
          'w-full rounded-md border border-line bg-panel px-3 py-2 text-[13px] text-ink placeholder:text-ink-faint focus:border-accent/60 focus:outline-none focus:ring-1 focus:ring-accent/40',
          mono && 'font-mono',
        )}
      />
    </div>
  )
}
