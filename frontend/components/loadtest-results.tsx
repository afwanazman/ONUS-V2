'use client'

import { useState, useEffect, useCallback } from 'react'
import {
  Activity, Clock, AlertTriangle, CheckCircle2, XCircle,
  TrendingUp, TrendingDown, Gauge, BarChart3, Zap,
  StopCircle, Loader2, ArrowRight, Info,
} from 'lucide-react'
import {
  getLoadTestStatus, getLoadTestResults, cancelLoadTest,
  type LoadTestStatusResponse, type LoadTestResultsResponse,
  type LoadTestTimeseriesPoint,
} from '@/lib/api'
import { Panel } from '@/components/ui'
import { cn } from '@/lib/format'

// ── Mini sparkline (inline SVG, no Recharts dep for the status page) ────────
function Sparkline({ data, dataKey, color = 'var(--color-accent)', height = 40 }: {
  data: LoadTestTimeseriesPoint[]
  dataKey: keyof LoadTestTimeseriesPoint
  color?: string
  height?: number
}) {
  if (!data || data.length < 2) return null
  const vals = data.map(d => Number(d[dataKey]) || 0)
  const max = Math.max(...vals, 1)
  const w = 200
  const points = vals.map((v, i) => `${(i / (vals.length - 1)) * w},${height - (v / max) * height}`).join(' ')
  return (
    <svg viewBox={`0 0 ${w} ${height}`} className="w-full" preserveAspectRatio="none">
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

// ── Metric card ─────────────────────────────────────────────────────────────
function MetricCard({ label, value, unit, sub, trend, className }: {
  label: string
  value: string | number
  unit?: string
  sub?: string
  trend?: 'up' | 'down' | 'neutral'
  className?: string
}) {
  return (
    <Panel className={cn('p-4', className)}>
      <div className="mb-1 text-xs font-semibold uppercase tracking-wider text-ink-dim">{label}</div>
      <div className="flex items-baseline gap-1.5">
        <span className="font-mono text-2xl font-bold text-ink">{value}</span>
        {unit && <span className="text-sm text-ink-dim">{unit}</span>}
        {trend === 'up' && <TrendingUp className="ml-auto h-4 w-4 text-red-500" />}
        {trend === 'down' && <TrendingDown className="ml-auto h-4 w-4 text-green-600" />}
      </div>
      {sub && <div className="mt-1 text-xs text-ink-faint">{sub}</div>}
    </Panel>
  )
}

// ── Performance gauge (SVG arc) ─────────────────────────────────────────────
// `score` is the backend's stored value. null means "never computed" (the run
// failed before producing metrics) and renders as an explicit absence - showing
// 0 there would claim the target scored the worst possible result, a different
// and much stronger statement than "we have no number". Bands use the severity
// tokens so this reads in the same colour language as the scan report's risk
// ring rather than raw Tailwind greens and reds.
function PerformanceGauge({ score }: { score: number | null }) {
  const radius = 60
  const circumference = Math.PI * radius  // semi-circle
  const has = score !== null
  const offset = circumference * (1 - (score ?? 0) / 100)
  const color = !has
    ? 'var(--color-ink-faint)'
    : score >= 80
      ? 'var(--color-cyan)'
      : score >= 60
        ? 'var(--color-med)'
        : score >= 40
          ? 'var(--color-high)'
          : 'var(--color-crit)'

  return (
    <div className="flex flex-col items-center">
      <svg viewBox="0 0 140 80" className="w-40" aria-hidden="true">
        <path
          d="M 10 70 A 60 60 0 0 1 130 70"
          fill="none"
          stroke="var(--color-raised-2)"
          strokeWidth="10"
          strokeLinecap="round"
        />
        {has && (
          <path
            d="M 10 70 A 60 60 0 0 1 130 70"
            fill="none"
            stroke={color}
            strokeWidth="10"
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={offset}
            className="transition-all duration-1000 ease-out"
          />
        )}
      </svg>
      <div className="-mt-6 text-center">
        <span className="tnum font-mono text-3xl font-bold" style={{ color }}>
          {has ? score : '--'}
        </span>
        <span className="text-sm text-ink-dim">/100</span>
      </div>
      <div className="signage mt-2 text-[10.5px] text-ink-faint">Performance score</div>
    </div>
  )
}

function formatMs(ms: number): string {
  if (ms < 1) return `${ms.toFixed(2)}ms`
  if (ms < 1000) return `${ms.toFixed(0)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function formatRate(rate: number): string {
  return `${(rate * 100).toFixed(2)}%`
}

// ═══════════════════════════════════════════════════════════════════════════
// Main component — handles both live status polling and completed results
// ═══════════════════════════════════════════════════════════════════════════

export function LoadTestResults({ jobId }: { jobId: string }) {
  const [status, setStatus] = useState<LoadTestStatusResponse | null>(null)
  const [results, setResults] = useState<LoadTestResultsResponse | null>(null)
  const [cancelling, setCancelling] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isTerminal = status?.status === 'complete' || status?.status === 'failed' || status?.status === 'cancelled'

  // Poll status while running
  useEffect(() => {
    let cancelled = false

    const poll = async () => {
      try {
        const s = await getLoadTestStatus(jobId)
        if (!cancelled) setStatus(s)

        if (s.status === 'complete' || s.status === 'failed') {
          try {
            const r = await getLoadTestResults(jobId)
            if (!cancelled) setResults(r)
          } catch {
            // Results might not be ready yet for failed tests
          }
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load status')
      }
    }

    poll()
    const interval = setInterval(poll, isTerminal ? 60000 : 2000)
    return () => { cancelled = true; clearInterval(interval) }
  }, [jobId, isTerminal])

  const handleCancel = useCallback(async () => {
    setCancelling(true)
    try {
      await cancelLoadTest(jobId)
      const s = await getLoadTestStatus(jobId)
      setStatus(s)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to cancel')
    } finally {
      setCancelling(false)
    }
  }, [jobId])

  if (error && !status) {
    return (
      <div className="mx-auto max-w-4xl px-6 py-12">
        <div className="rounded-lg border border-red-200 bg-red-50 p-6 text-sm text-red-700">
          {error}
        </div>
      </div>
    )
  }

  if (!status) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-ink-dim" />
      </div>
    )
  }

  const metrics = results?.metrics
  const timeseries = results?.timeseries

  return (
    <div className="mx-auto max-w-5xl px-6 py-12">
      {/* Header */}
      <div className="mb-8 flex items-start justify-between">
        <div>
          <h1 className="font-display text-3xl tracking-tight text-ink">
            Load <em className="text-indigo">Test</em>
          </h1>
          <p className="mt-1 font-mono text-sm text-ink-dim">{status.target_url}</p>
          <div className="mt-2 flex items-center gap-3">
            <StatusPill status={status.status} />
            <span className="text-xs text-ink-faint">
              {status.scenario} · {status.virtual_users} VUs · {status.duration_seconds}s
            </span>
          </div>
        </div>

        {!isTerminal && (
          <button
            type="button"
            onClick={handleCancel}
            disabled={cancelling}
            className="flex items-center gap-2 rounded-md border border-red-300 px-4 py-2 text-sm font-medium text-red-600 transition-colors hover:bg-red-50"
          >
            {cancelling ? <Loader2 className="h-4 w-4 animate-spin" /> : <StopCircle className="h-4 w-4" />}
            Cancel
          </button>
        )}
      </div>

      {/* Progress bar (while running) */}
      {!isTerminal && (
        <div className="mb-8">
          <div className="mb-2 flex items-center justify-between text-xs text-ink-dim">
            <span>Progress</span>
            <span>{status.progress}%</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-raised-2">
            <div
              className="h-full rounded-full bg-accent transition-all duration-500"
              style={{ width: `${status.progress}%` }}
            />
          </div>

          {/* Live metrics while running */}
          {(status.current_rps != null || status.current_latency_p95 != null) && (
            <div className="mt-4 grid grid-cols-4 gap-3">
              {status.current_rps != null && (
                <MetricCard label="Live RPS" value={status.current_rps.toFixed(1)} unit="req/s" />
              )}
              {status.current_latency_p95 != null && (
                <MetricCard label="p95 Latency" value={formatMs(status.current_latency_p95)} />
              )}
              {status.current_error_rate != null && (
                <MetricCard label="Error Rate" value={formatRate(status.current_error_rate)} />
              )}
              {status.current_vus != null && (
                <MetricCard label="Active VUs" value={status.current_vus} />
              )}
            </div>
          )}
        </div>
      )}

      {/* Results (complete or failed) */}
      {isTerminal && status.status !== 'cancelled' && (
        <>
          {/* Performance Score + Key Metrics */}
          <div className="mb-8 grid grid-cols-1 gap-6 lg:grid-cols-3">
            {/* Score gauge */}
            <Panel className="flex items-center justify-center p-6">
              <PerformanceGauge score={results?.performance_score ?? null} />
            </Panel>

            {/* Key metrics grid */}
            <div className="col-span-2 grid grid-cols-2 gap-3 sm:grid-cols-3">
              {metrics && (
                <>
                  <MetricCard
                    label="p95 Latency"
                    value={formatMs(metrics.http_req_duration_p95)}
                    sub={`avg ${formatMs(metrics.http_req_duration_avg)}`}
                    trend={metrics.http_req_duration_p95 > 1000 ? 'up' : undefined}
                  />
                  <MetricCard
                    label="p99 Latency"
                    value={formatMs(metrics.http_req_duration_p99)}
                    sub={`max ${formatMs(metrics.http_req_duration_max)}`}
                  />
                  <MetricCard
                    label="RPS"
                    value={metrics.http_reqs_per_second.toFixed(1)}
                    unit="req/s"
                    sub={`${metrics.total_requests.toLocaleString()} total`}
                  />
                  <MetricCard
                    label="Error Rate"
                    value={formatRate(metrics.http_req_failed_rate)}
                    trend={metrics.http_req_failed_rate > 0.01 ? 'up' : 'down'}
                  />
                  <MetricCard
                    label="Data Transfer"
                    value={`${metrics.total_data_received_mb.toFixed(1)}`}
                    unit="MB received"
                  />
                  <MetricCard
                    label="Max VUs"
                    value={metrics.vus_max}
                    sub={`${metrics.iterations.toLocaleString()} iterations`}
                  />
                </>
              )}
            </div>
          </div>

          {/* Latency Distribution */}
          {metrics && (
            <Panel className="mb-8 p-6">
              <h2 className="mb-4 text-xs font-semibold uppercase tracking-wider text-ink-dim">
                Latency Distribution
              </h2>
              <div className="flex items-end gap-1">
                {[
                  { label: 'min', value: metrics.http_req_duration_min },
                  { label: 'p50', value: metrics.http_req_duration_p50 },
                  { label: 'avg', value: metrics.http_req_duration_avg },
                  { label: 'p90', value: metrics.http_req_duration_p90 },
                  { label: 'p95', value: metrics.http_req_duration_p95 },
                  { label: 'p99', value: metrics.http_req_duration_p99 },
                  { label: 'max', value: metrics.http_req_duration_max },
                ].map(({ label, value }) => {
                  const maxVal = metrics.http_req_duration_max || 1
                  const pct = Math.max(4, (value / maxVal) * 100)
                  const isHighlight = label === 'p95'
                  return (
                    <div key={label} className="flex flex-1 flex-col items-center gap-1">
                      <span className="font-mono text-[10px] text-ink-dim">{formatMs(value)}</span>
                      <div
                        className={cn(
                          'w-full rounded-t-sm transition-all',
                          isHighlight ? 'bg-accent' : 'bg-accent/30',
                        )}
                        style={{ height: `${pct}px`, minHeight: '4px', maxHeight: '80px' }}
                      />
                      <span className={cn(
                        'text-[10px] font-medium',
                        isHighlight ? 'text-accent' : 'text-ink-faint',
                      )}>{label}</span>
                    </div>
                  )
                })}
              </div>
            </Panel>
          )}

          {/* Timeseries Charts */}
          {timeseries && timeseries.length > 2 && (
            <div className="mb-8 grid grid-cols-1 gap-4 lg:grid-cols-2">
              <Panel className="p-4">
                <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-ink-dim">
                  Requests/sec
                </div>
                <Sparkline data={timeseries} dataKey="rps" color="var(--color-accent)" height={60} />
              </Panel>
              <Panel className="p-4">
                <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-ink-dim">
                  p95 Latency
                </div>
                <Sparkline data={timeseries} dataKey="latency_p95" color="var(--color-indigo)" height={60} />
              </Panel>
              <Panel className="p-4">
                <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-ink-dim">
                  Active VUs
                </div>
                <Sparkline data={timeseries} dataKey="vus" color="var(--color-lime-deep)" height={60} />
              </Panel>
              <Panel className="p-4">
                <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-ink-dim">
                  Errors
                </div>
                <Sparkline data={timeseries} dataKey="errors" color="#ef4444" height={60} />
              </Panel>
            </div>
          )}

          {/* Breaking Point */}
          {results?.breaking_point_vus != null && (
            <div className="onus-card mb-8 rounded-lg border-2 border-amber-300 bg-amber-50/50 p-6">
              <div className="flex items-start gap-3">
                <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" />
                <div>
                  <h3 className="font-semibold text-ink">
                    Breaking Point Detected: {results.breaking_point_vus} VUs
                  </h3>
                  <p className="mt-1 text-sm text-ink-dim">
                    Performance degradation was observed at approximately {results.breaking_point_vus} concurrent
                    users. Consider scaling your infrastructure before this level of traffic.
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* Thresholds */}
          {results?.thresholds_passed != null && (
            <div className={cn(
              'onus-card mb-8 rounded-lg border-2 p-6',
              results.thresholds_passed
                ? 'border-green-300 bg-green-50/50'
                : 'border-red-300 bg-red-50/50',
            )}>
              <div className="flex items-center gap-3">
                {results.thresholds_passed ? (
                  <CheckCircle2 className="h-5 w-5 text-green-600" />
                ) : (
                  <XCircle className="h-5 w-5 text-red-600" />
                )}
                <span className="font-semibold text-ink">
                  Thresholds {results.thresholds_passed ? 'Passed' : 'Failed'}
                </span>
              </div>
            </div>
          )}

          {/* AI Analysis */}
          {results?.ai_analysis && (
            <Panel className="mb-8 p-6">
              <h2 className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-ink-dim">
                <Info className="h-3.5 w-3.5" /> Analysis
              </h2>
              <p className="text-sm leading-relaxed text-ink">{results.ai_analysis}</p>
            </Panel>
          )}

          {/* Recommendations */}
          {results?.ai_recommendations && results.ai_recommendations.length > 0 && (
            <Panel className="p-6">
              <h2 className="mb-4 text-xs font-semibold uppercase tracking-wider text-ink-dim">
                Recommendations
              </h2>
              <ul className="space-y-3">
                {results.ai_recommendations.map((rec, i) => (
                  <li key={i} className="flex items-start gap-3">
                    <ArrowRight className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
                    <span className="text-sm text-ink">{rec}</span>
                  </li>
                ))}
              </ul>
            </Panel>
          )}
        </>
      )}

      {/* Cancelled */}
      {status.status === 'cancelled' && (
        <Panel className="p-8 text-center">
          <XCircle className="mx-auto h-12 w-12 text-ink-dim" />
          <h2 className="mt-4 font-display text-xl text-ink">Load Test Cancelled</h2>
          <p className="mt-2 text-sm text-ink-dim">This test was cancelled before completion.</p>
        </Panel>
      )}
    </div>
  )
}

// ── Status pill ─────────────────────────────────────────────────────────────
function StatusPill({ status }: { status: string }) {
  const config: Record<string, { bg: string; text: string; icon: typeof Activity }> = {
    queued: { bg: 'bg-ink/8', text: 'text-ink-dim', icon: Clock },
    running: { bg: 'bg-accent/12', text: 'text-accent', icon: Activity },
    warmup: { bg: 'bg-indigo/12', text: 'text-indigo', icon: TrendingUp },
    analysing: { bg: 'bg-indigo/12', text: 'text-indigo', icon: BarChart3 },
    complete: { bg: 'bg-green-100', text: 'text-green-700', icon: CheckCircle2 },
    failed: { bg: 'bg-red-100', text: 'text-red-700', icon: XCircle },
    cancelled: { bg: 'bg-ink/8', text: 'text-ink-dim', icon: StopCircle },
  }
  const c = config[status] || config.queued
  const Icon = c.icon

  return (
    <span className={cn('inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium', c.bg, c.text)}>
      <Icon className="h-3 w-3" />
      {status}
    </span>
  )
}

// A client-side reimplementation of compute_performance_score used to live
// here. It was necessarily wrong: the browser is never sent the configured
// pass/fail thresholds, which are 15% of the score, so the copy hardcoded that
// component to a neutral 50 and silently disagreed with the backend whenever a
// latency or error budget was set. The score is now read from
// `results.performance_score`, which is the value the orchestrator computed and
// stored - one implementation, one number.
