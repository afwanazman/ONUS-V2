import Link from 'next/link'
import { ShieldAlert, Gauge, ArrowRight } from 'lucide-react'
import { Panel } from '@/components/ui'
import { Plate } from '@/components/decor'

export default function Page() {
  return (
    <div className="relative w-full overflow-x-clip min-h-screen">
      <Plate src="magnifier-blueprint" rotate={5} opacity={0.24} delay={0}
        className="right-[2%] top-[10%] hidden h-[440px] w-[440px] xl:block" />
      <Plate src="theodolite" rotate={-7} opacity={0.24} delay={3}
        className="left-[2%] bottom-[10%] hidden h-[360px] w-[360px] xl:block" />

      <div className="mx-auto flex min-h-[80vh] w-full max-w-[800px] flex-col justify-center px-6 py-20 relative z-10">
        <header className="mb-12 text-center onus-fade-up">
          <h1 className="font-display text-4xl tracking-tight text-ink mb-4">
            Unified Security <em className="text-indigo">Operations</em>
          </h1>
          <p className="mx-auto max-w-[500px] text-sm leading-relaxed text-ink-dim">
            Select an assessment engine to begin.
          </p>
        </header>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 onus-fade-up" style={{ animationDelay: '100ms' }}>
          <Link href="/scan/new" className="group block focus:outline-none">
            <Panel className="h-full p-8 transition-all duration-300 hover:border-accent/50 hover:bg-canvas group-focus:ring-2 group-focus:ring-accent/40 relative overflow-hidden">
              <div className="absolute top-0 right-0 p-6 opacity-0 transform translate-x-4 transition-all duration-300 group-hover:opacity-100 group-hover:translate-x-0">
                <ArrowRight className="h-5 w-5 text-accent" />
              </div>
              <div className="mb-6 inline-flex h-12 w-12 items-center justify-center rounded-lg bg-accent/10 text-accent">
                <ShieldAlert className="h-6 w-6" />
              </div>
              <h2 className="mb-3 text-xl font-semibold text-ink">VAPT Scan</h2>
              <p className="text-sm text-ink-dim leading-relaxed">
                Run 8 parallel scanning modules to identify and deterministically score vulnerabilities in your web applications.
              </p>
            </Panel>
          </Link>

          <Link href="/loadtest/new" className="group block focus:outline-none">
            <Panel className="h-full p-8 transition-all duration-300 hover:border-indigo/50 hover:bg-canvas group-focus:ring-2 group-focus:ring-indigo/40 relative overflow-hidden">
              <div className="absolute top-0 right-0 p-6 opacity-0 transform translate-x-4 transition-all duration-300 group-hover:opacity-100 group-hover:translate-x-0">
                <ArrowRight className="h-5 w-5 text-indigo" />
              </div>
              <div className="mb-6 inline-flex h-12 w-12 items-center justify-center rounded-lg bg-indigo/10 text-indigo">
                <Gauge className="h-6 w-6" />
              </div>
              <h2 className="mb-3 text-xl font-semibold text-ink">Load Test</h2>
              <p className="text-sm text-ink-dim leading-relaxed">
                Find the breaking point of your infrastructure. Run configurable traffic scenarios with real-time performance tracking.
              </p>
            </Panel>
          </Link>
        </div>
      </div>
    </div>
  )
}
