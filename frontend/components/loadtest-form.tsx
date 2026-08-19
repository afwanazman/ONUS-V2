'use client'

import { useState, useEffect, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  TrendingUp, Minus, Zap, Clock, AlertTriangle,
  Play, ChevronDown, Plus, Trash2, Loader2, Lock, Activity,
} from 'lucide-react'
import { Panel } from '@/components/ui'
import {
  submitLoadTest, getLoadTestScenarios,
  type LoadTestScenarioInfo, type LoadTestScenarioType, type LoadTestRequestBody,
} from '@/lib/api'
import { cn } from '@/lib/format'

const SCENARIO_ICONS: Record<string, typeof TrendingUp> = {
  'trending-up': TrendingUp,
  'minus': Minus,
  'zap': Zap,
  'clock': Clock,
  'alert-triangle': AlertTriangle,
  'spider': Zap,
}

const HTTP_METHODS = ['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'] as const

export function LoadTestForm() {
  const router = useRouter()
  const [scenarios, setScenarios] = useState<LoadTestScenarioInfo[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Form state
  const [targetUrl, setTargetUrl] = useState('')
  const [scenario, setScenario] = useState<LoadTestScenarioType>('ramp')
  const [vus, setVus] = useState(50)
  const [duration, setDuration] = useState(30)
  const [method, setMethod] = useState<string>('GET')
  const [authorized, setAuthorized] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [headers, setHeaders] = useState<{ key: string; value: string }[]>([])
  const [requestBody, setRequestBody] = useState('')
  const [notes, setNotes] = useState('')
  const [thresholdP95, setThresholdP95] = useState<string>('')
  const [thresholdErrorRate, setThresholdErrorRate] = useState<string>('')

  // Auth state
  const [loginUrl, setLoginUrl] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [loginType, setLoginType] = useState<'auto' | 'form' | 'json'>('auto')
  const [usernameField, setUsernameField] = useState('username')
  const [passwordField, setPasswordField] = useState('password')
  const [tokenJsonPath, setTokenJsonPath] = useState('')
  const [checksOpen, setChecksOpen] = useState(false)

  const authComplete = loginUrl.trim() !== '' && username.trim() !== '' && password.trim() !== ''

  useEffect(() => {
    getLoadTestScenarios().then(setScenarios).catch(() => {})
  }, [])

  const handleSubmit = useCallback(async () => {
    if (!targetUrl.trim()) {
      setError('Target URL is required')
      return
    }
    if (!authorized) {
      setError('You must confirm authorization to load test this target')
      return
    }

    setLoading(true)
    setError(null)

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

      // Headers
      const validHeaders = headers.filter(h => h.key.trim() && h.value.trim())
      if (validHeaders.length > 0) {
        body.headers = Object.fromEntries(validHeaders.map(h => [h.key.trim(), h.value.trim()]))
      }

      // Body
      if (requestBody.trim() && ['POST', 'PUT', 'PATCH'].includes(method)) {
        body.request_body = requestBody.trim()
      }

      // Thresholds
      const thresholds: Record<string, number> = {}
      if (thresholdP95 && !isNaN(Number(thresholdP95))) {
        thresholds.http_req_duration_p95 = Number(thresholdP95)
      }
      if (thresholdErrorRate && !isNaN(Number(thresholdErrorRate))) {
        thresholds.http_req_failed_rate = Number(thresholdErrorRate) / 100
      }
      if (Object.keys(thresholds).length > 0) {
        body.thresholds = thresholds
      }

      if (authComplete) {
        body.auth = {
          login_url: loginUrl.trim(),
          username: username.trim(),
          password,
          login_type: loginType,
        }
        if (usernameField.trim()) body.auth.username_field = usernameField.trim()
        if (passwordField.trim()) body.auth.password_field = passwordField.trim()
        if (loginType === 'json' && tokenJsonPath.trim()) body.auth.token_json_path = tokenJsonPath.trim()
      }

      const res = await submitLoadTest(body)
      router.push(`/loadtest/${res.job_id}/status`)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to start load test')
    } finally {
      setLoading(false)
    }
  }, [targetUrl, authorized, scenario, vus, duration, method, headers, requestBody, notes, thresholdP95, thresholdErrorRate, router])

  const selectedScenario = scenarios.find(s => s.id === scenario)
  const selectedScenarioIcon = selectedScenario ? SCENARIO_ICONS[selectedScenario.icon_hint] || TrendingUp : TrendingUp

  const activeModules = [
    {
      id: 'k6-core',
      label: 'k6 Generator',
      icon: Activity,
      description: 'The core k6 engine generates the virtual users, executes the payload, and orchestrates the load metrics.',
    },
    {
      id: 'scenario',
      label: `Scenario: ${selectedScenario?.label || 'Ramp Up/Down'}`,
      icon: selectedScenarioIcon,
      description: selectedScenario?.description || 'Virtual user scaling strategy and traffic shaping.',
    },
    ...((thresholdP95 || thresholdErrorRate) ? [{
      id: 'thresholds',
      label: 'Threshold Monitor',
      icon: AlertTriangle,
      description: 'Monitors the live test to verify latency and error rates stay within your configured safety limits.',
    }] : []),
    ...(authComplete ? [{
      id: 'auth',
      label: 'Pre-Flight Auth',
      icon: Lock,
      description: 'Will authenticate to the target and inject credentials before starting the test payload.',
    }] : [])
  ]

  return (
    <div className="mx-auto max-w-3xl px-6 py-12">
      {/* Header */}
      <div className="mb-10">
        <h1 className="font-display text-4xl tracking-tight text-ink">
          Load <em className="text-indigo">Test</em>
        </h1>
        <p className="mt-2 text-sm text-ink-dim">
          Stress-test your endpoints. Find the breaking point before your users do.
        </p>
      </div>

      {/* Target URL */}
      <Panel className="mb-6 p-6">
        <label className="mb-2 block text-xs font-semibold uppercase tracking-wider text-ink-dim">
          Target URL
        </label>
        <input
          type="url"
          value={targetUrl}
          onChange={e => setTargetUrl(e.target.value)}
          placeholder="https://api.example.com/endpoint"
          className="w-full rounded-md border border-line bg-canvas px-4 py-3 font-mono text-sm text-ink placeholder:text-ink-faint focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
        />
        <p className="mt-2 text-xs text-ink-faint">
          The URL to send requests to. Must be reachable from the ONUS server.
        </p>
      </Panel>

      {/* Scenario Selection */}
      <Panel className="mb-6 p-6">
        <label className="mb-4 block text-xs font-semibold uppercase tracking-wider text-ink-dim">
          Scenario
        </label>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {(scenarios.length > 0 ? scenarios : [
            { id: 'ramp' as const, label: 'Ramp Up/Down', description: 'Gradual VU increase', icon_hint: 'trending-up' },
            { id: 'constant' as const, label: 'Constant Load', description: 'Fixed VUs', icon_hint: 'minus' },
            { id: 'spike' as const, label: 'Spike Test', description: 'Sudden burst', icon_hint: 'zap' },
            { id: 'soak' as const, label: 'Soak Test', description: 'Extended duration', icon_hint: 'clock' },
            { id: 'stress' as const, label: 'Stress Test', description: 'Find breaking point', icon_hint: 'alert-triangle' },
            { id: 'spider' as const, label: 'Spider Test', description: 'Random walk discovery', icon_hint: 'spider' },
          ]).map(s => {
            const Icon = SCENARIO_ICONS[s.icon_hint] || TrendingUp
            const active = scenario === s.id
            return (
              <button
                key={s.id}
                type="button"
                onClick={() => setScenario(s.id as LoadTestScenarioType)}
                className={cn(
                  'flex items-start gap-3 rounded-md border p-3 text-left transition-all',
                  active
                    ? 'border-accent bg-accent/8 ring-1 ring-accent'
                    : 'border-line hover:border-ink-dim/30 hover:bg-canvas',
                )}
              >
                <Icon className={cn('mt-0.5 h-4 w-4 shrink-0', active ? 'text-accent' : 'text-ink-dim')} strokeWidth={1.7} />
                <div>
                  <div className={cn('text-sm font-medium', active ? 'text-accent' : 'text-ink')}>{s.label}</div>
                  <div className="mt-0.5 text-xs text-ink-faint line-clamp-2">{s.description}</div>
                </div>
              </button>
            )
          })}
        </div>
        {selectedScenario && (
          <p className="mt-3 text-xs text-ink-dim">{selectedScenario.description}</p>
        )}
      </Panel>

      {/* VUs & Duration */}
      <div className="mb-6 grid grid-cols-2 gap-4">
        <Panel className="p-6">
          <label className="mb-2 block text-xs font-semibold uppercase tracking-wider text-ink-dim">
            Virtual Users
          </label>
          <input
            type="number"
            value={vus}
            onChange={e => setVus(Math.max(1, Math.min(1000, Number(e.target.value) || 1)))}
            min={1}
            max={1000}
            className="w-full rounded-md border border-line bg-canvas px-4 py-3 font-mono text-sm text-ink focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
          />
          <p className="mt-2 text-xs text-ink-faint">Concurrent users (1–1000)</p>
        </Panel>
        <Panel className="p-6">
          <label className="mb-2 block text-xs font-semibold uppercase tracking-wider text-ink-dim">
            Duration
          </label>
          <div className="flex items-center gap-2">
            <input
              type="number"
              value={duration}
              onChange={e => setDuration(Math.max(5, Math.min(3600, Number(e.target.value) || 5)))}
              min={5}
              max={3600}
              className="w-full rounded-md border border-line bg-canvas px-4 py-3 font-mono text-sm text-ink focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
            />
            <span className="shrink-0 text-sm text-ink-dim">seconds</span>
          </div>
          <p className="mt-2 text-xs text-ink-faint">Test duration (5s–1h)</p>
        </Panel>
      </div>

      {/* HTTP Method */}
      <Panel className="mb-6 p-6">
        <label className="mb-2 block text-xs font-semibold uppercase tracking-wider text-ink-dim">
          HTTP Method
        </label>
        <div className="flex flex-wrap gap-2">
          {HTTP_METHODS.map(m => (
            <button
              key={m}
              type="button"
              onClick={() => setMethod(m)}
              className={cn(
                'rounded-md border px-3 py-1.5 font-mono text-xs font-medium transition-all',
                method === m
                  ? 'border-accent bg-accent/8 text-accent'
                  : 'border-line text-ink-dim hover:border-ink-dim/30',
              )}
            >
              {m}
            </button>
          ))}
        </div>
      </Panel>

      {/* Advanced Options */}
      <div className="mb-6">
        <button
          type="button"
          onClick={() => setShowAdvanced(!showAdvanced)}
          className="flex items-center gap-2 text-sm text-ink-dim transition-colors hover:text-ink"
        >
          <ChevronDown className={cn('h-4 w-4 transition-transform', showAdvanced && 'rotate-180')} />
          Advanced Options
        </button>

        {showAdvanced && (
          <div className="mt-4 space-y-4">
            {/* Authentication */}
            <Panel className="p-6">
              <label className="mb-3 block text-xs font-semibold uppercase tracking-wider text-ink-dim">
                Authentication
              </label>
              <div className="space-y-3">
                <div>
                  <label className="mb-1 block text-xs text-ink-dim">Login URL</label>
                  <input
                    type="url"
                    value={loginUrl}
                    onChange={e => setLoginUrl(e.target.value)}
                    placeholder="https://api.example.com/login"
                    className="w-full rounded-md border border-line bg-canvas px-3 py-2 font-mono text-xs text-ink placeholder:text-ink-faint focus:border-accent focus:outline-none"
                  />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="mb-1 block text-xs text-ink-dim">Username / Email</label>
                    <input
                      type="text"
                      value={username}
                      onChange={e => setUsername(e.target.value)}
                      placeholder="admin@example.com"
                      className="w-full rounded-md border border-line bg-canvas px-3 py-2 font-mono text-xs text-ink placeholder:text-ink-faint focus:border-accent focus:outline-none"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-xs text-ink-dim">Password</label>
                    <input
                      type="password"
                      value={password}
                      onChange={e => setPassword(e.target.value)}
                      placeholder="••••••••"
                      className="w-full rounded-md border border-line bg-canvas px-3 py-2 font-mono text-xs text-ink placeholder:text-ink-faint focus:border-accent focus:outline-none"
                    />
                  </div>
                </div>

                {loginUrl && (
                  <div className="mt-4 border-t border-line pt-4">
                    <label className="mb-2 block text-xs text-ink-dim">Login Method</label>
                    <div className="mb-3 flex gap-2">
                      {(['auto', 'form', 'json'] as const).map(t => (
                        <button
                          key={t}
                          type="button"
                          onClick={() => setLoginType(t)}
                          className={cn(
                            'rounded-[3px] border px-2.5 py-1 text-[11px] font-medium transition-colors',
                            loginType === t
                              ? 'border-accent bg-accent/10 text-accent'
                              : 'border-line text-ink-dim hover:text-ink',
                          )}
                        >
                          {t === 'auto' ? 'Auto-Detect' : t === 'form' ? 'HTML Form' : 'JSON API'}
                        </button>
                      ))}
                    </div>

                    {(loginType === 'form' || loginType === 'json') && (
                      <div className="grid grid-cols-2 gap-3 mb-3">
                        <div>
                          <label className="mb-1 block text-[11px] text-ink-faint">Username field</label>
                          <input
                            value={usernameField}
                            onChange={e => setUsernameField(e.target.value)}
                            placeholder="username"
                            className="w-full rounded-[3px] border border-line bg-canvas px-2.5 py-1.5 font-mono text-[11px] text-ink focus:border-accent focus:outline-none"
                          />
                        </div>
                        <div>
                          <label className="mb-1 block text-[11px] text-ink-faint">Password field</label>
                          <input
                            value={passwordField}
                            onChange={e => setPasswordField(e.target.value)}
                            placeholder="password"
                            className="w-full rounded-[3px] border border-line bg-canvas px-2.5 py-1.5 font-mono text-[11px] text-ink focus:border-accent focus:outline-none"
                          />
                        </div>
                      </div>
                    )}

                    {loginType === 'json' && (
                      <div>
                        <label className="mb-1 block text-[11px] text-ink-faint">Token JSON path (optional)</label>
                        <input
                          value={tokenJsonPath}
                          onChange={e => setTokenJsonPath(e.target.value)}
                          placeholder="data.token"
                          className="w-full rounded-[3px] border border-line bg-canvas px-2.5 py-1.5 font-mono text-[11px] text-ink focus:border-accent focus:outline-none"
                        />
                      </div>
                    )}
                  </div>
                )}
              </div>
            </Panel>

            {/* Custom Headers */}
            <Panel className="p-6">
              <div className="mb-3 flex items-center justify-between">
                <label className="text-xs font-semibold uppercase tracking-wider text-ink-dim">
                  Custom Headers
                </label>
                <button
                  type="button"
                  onClick={() => setHeaders([...headers, { key: '', value: '' }])}
                  className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-accent transition-colors hover:bg-accent/8"
                >
                  <Plus className="h-3 w-3" /> Add
                </button>
              </div>
              {headers.map((h, i) => (
                <div key={i} className="mb-2 flex gap-2">
                  <input
                    value={h.key}
                    onChange={e => {
                      const next = [...headers]
                      next[i] = { ...next[i], key: e.target.value }
                      setHeaders(next)
                    }}
                    placeholder="Header name"
                    className="flex-1 rounded-md border border-line bg-canvas px-3 py-2 font-mono text-xs text-ink placeholder:text-ink-faint focus:border-accent focus:outline-none"
                  />
                  <input
                    value={h.value}
                    onChange={e => {
                      const next = [...headers]
                      next[i] = { ...next[i], value: e.target.value }
                      setHeaders(next)
                    }}
                    placeholder="Value"
                    className="flex-1 rounded-md border border-line bg-canvas px-3 py-2 font-mono text-xs text-ink placeholder:text-ink-faint focus:border-accent focus:outline-none"
                  />
                  <button
                    type="button"
                    onClick={() => setHeaders(headers.filter((_, j) => j !== i))}
                    className="rounded-md p-2 text-ink-faint transition-colors hover:text-red-500"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </Panel>

            {/* Request Body */}
            {['POST', 'PUT', 'PATCH'].includes(method) && (
              <Panel className="p-6">
                <label className="mb-2 block text-xs font-semibold uppercase tracking-wider text-ink-dim">
                  Request Body
                </label>
                <textarea
                  value={requestBody}
                  onChange={e => setRequestBody(e.target.value)}
                  placeholder='{"key": "value"}'
                  rows={4}
                  className="w-full rounded-md border border-line bg-canvas px-4 py-3 font-mono text-xs text-ink placeholder:text-ink-faint focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
                />
              </Panel>
            )}

            {/* Thresholds */}
            <Panel className="p-6">
              <label className="mb-3 block text-xs font-semibold uppercase tracking-wider text-ink-dim">
                Pass/Fail Thresholds
              </label>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="mb-1 block text-xs text-ink-dim">p95 latency (ms)</label>
                  <input
                    type="number"
                    value={thresholdP95}
                    onChange={e => setThresholdP95(e.target.value)}
                    placeholder="500"
                    className="w-full rounded-md border border-line bg-canvas px-3 py-2 font-mono text-xs text-ink placeholder:text-ink-faint focus:border-accent focus:outline-none"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs text-ink-dim">Max error rate (%)</label>
                  <input
                    type="number"
                    value={thresholdErrorRate}
                    onChange={e => setThresholdErrorRate(e.target.value)}
                    placeholder="1"
                    step="0.1"
                    className="w-full rounded-md border border-line bg-canvas px-3 py-2 font-mono text-xs text-ink placeholder:text-ink-faint focus:border-accent focus:outline-none"
                  />
                </div>
              </div>
              <p className="mt-2 text-xs text-ink-faint">
                Tests exceeding these thresholds will be marked as failed in the report.
              </p>
            </Panel>

            {/* Notes */}
            <Panel className="p-6">
              <label className="mb-2 block text-xs font-semibold uppercase tracking-wider text-ink-dim">
                Notes
              </label>
              <textarea
                value={notes}
                onChange={e => setNotes(e.target.value)}
                placeholder="Optional notes about this test..."
                rows={2}
                className="w-full rounded-md border border-line bg-canvas px-4 py-3 text-sm text-ink placeholder:text-ink-faint focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
              />
            </Panel>
          </div>
        )}
      </div>

      {/* Authorization & Submit */}
      <Panel className="p-6">
        <label className="flex items-start gap-3">
          <input
            type="checkbox"
            checked={authorized}
            onChange={e => setAuthorized(e.target.checked)}
            className="mt-0.5 h-4 w-4 rounded border-line accent-accent"
          />
          <span className="text-sm text-ink">
            I confirm that I am <strong>authorized</strong> to load test this target and understand
            that this will generate <strong>significant traffic</strong> against it.
          </span>
        </label>

        {error && (
          <div className="mt-4 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        )}

        <button
          type="button"
          onClick={handleSubmit}
          disabled={loading || !authorized || !targetUrl.trim()}
          className={cn(
            'mt-6 flex w-full items-center justify-center gap-2 rounded-md border-2 px-6 py-3 font-semibold transition-all',
            loading || !authorized || !targetUrl.trim()
              ? 'cursor-not-allowed border-line bg-raised-2 text-ink-faint'
              : 'border-ink bg-lime text-ink shadow-[3px_3px_0px_0px_var(--color-ink)] hover:shadow-[1px_1px_0px_0px_var(--color-ink)] hover:translate-x-[2px] hover:translate-y-[2px] active:shadow-none active:translate-x-[3px] active:translate-y-[3px]',
          )}
        >
          {loading ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              Starting…
            </>
          ) : (
            <>
              <Play className="h-4 w-4" />
              Start Load Test
            </>
          )}
        </button>
      </Panel>

      {/* Capability pills - dynamically generated based on form state */}
      {activeModules.length > 0 && (
        <div className="mt-8 onus-fade-up" style={{ animationDelay: '120ms' }}>
          {/* Module coverage as a systems panel */}
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
                    <Icon className="h-4 w-4 shrink-0 text-ink-dim" />
                    <span className="text-[12px] text-ink-dim">{m.label}</span>
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
              What does this test check?
              <ChevronDown className={cn('h-4 w-4 text-ink-faint transition-transform', checksOpen && 'rotate-180')} strokeWidth={1.6} />
            </button>
            {checksOpen && (
              <ul className="divide-y divide-line border-t border-line">
                {activeModules.map((m) => {
                  const Icon = m.icon
                  return (
                    <li key={m.id} className="flex gap-3 px-4 py-3">
                      <Icon className="mt-0.5 h-4 w-4 shrink-0 text-accent/70" />
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
      )}
    </div>
  )
}
