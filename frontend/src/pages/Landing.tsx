import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Cpu, ArrowRight, ShieldCheck, Gauge, Box } from 'lucide-react'

export function LandingPage() {
  return (
    <div className="min-h-screen bg-bg text-text">
      <div
        className="absolute inset-0 opacity-[0.05]"
        style={{
          backgroundImage:
            'radial-gradient(circle at 20% 20%, rgba(59,130,246,0.35), transparent 30%), radial-gradient(circle at 80% 0%, rgba(16,185,129,0.18), transparent 28%)',
        }}
      />
      <div className="relative max-w-6xl mx-auto px-6 py-10">
        <header className="flex items-center justify-between py-4">
          <div className="flex items-center gap-2">
            <Cpu className="h-5 w-5 text-blue" />
            <span className="font-mono text-sm font-medium">DesignPilot.MECH</span>
          </div>
          <div className="flex items-center gap-3">
            <Link to="/login" className="font-mono text-xs text-text-muted hover:text-text transition-colors">Log In</Link>
            <Link to="/signup" className="px-3 py-2 bg-blue text-white font-mono text-xs hover:bg-blue-dim transition-colors">Start Free</Link>
          </div>
        </header>

        <section className="pt-20 pb-16 max-w-3xl">
          <p className="label-xs mb-4">WEEK 5 FRONTEND FOUNDATION</p>
          <h1 className="font-mono text-4xl leading-tight max-w-2xl">
            Prompt a mechanical bracket, review three variants, and carry the math with it.
          </h1>
          <p className="mt-5 font-mono text-sm text-text-muted max-w-2xl leading-7">
            The local build now includes the landing shell, auth routes, studio, my-designs view,
            and settings screen. For secure local testing, generate designs after signing in.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link to="/signup" className="inline-flex items-center gap-2 px-4 py-2 bg-blue text-white font-mono text-xs hover:bg-blue-dim transition-colors">
              Open Studio <ArrowRight className="h-3.5 w-3.5" />
            </Link>
            <Link to="/login" className="inline-flex items-center gap-2 px-4 py-2 border border-border text-text font-mono text-xs hover:border-border-strong transition-colors">
              Sign In
            </Link>
          </div>
        </section>

        <section className="grid gap-4 md:grid-cols-3">
          <FeatureCard icon={<ShieldCheck className="h-4 w-4 text-green" />} title="Security core" text="AST validation, sandbox isolation, and authenticated API routes stay enforced in local dev." />
          <FeatureCard icon={<Gauge className="h-4 w-4 text-blue" />} title="Accuracy first" text="Variant metrics stay tied to deterministic formulas and Triple-Lock confidence." />
          <FeatureCard icon={<Box className="h-4 w-4 text-amber" />} title="Studio workflow" text="Landing, auth, dashboard, studio, and settings now connect as a coherent route shell." />
        </section>
      </div>
    </div>
  )
}

function FeatureCard({
  icon,
  title,
  text,
}: {
  icon: ReactNode
  title: string
  text: string
}) {
  return (
    <div className="border border-border bg-bg-2 p-5">
      <div className="mb-4">{icon}</div>
      <h2 className="font-mono text-sm mb-2">{title}</h2>
      <p className="font-mono text-xs text-text-muted leading-6">{text}</p>
    </div>
  )
}
