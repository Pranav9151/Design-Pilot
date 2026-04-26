import { motion } from 'framer-motion'
import { CheckCircle2, AlertTriangle, XCircle, Zap, Weight, DollarSign, ShieldCheck } from 'lucide-react'
import { cn, fmt, sfColor, bandColor } from '@/lib/utils'
import type { VariantResult } from '@/lib/api'

interface Props {
  variant: VariantResult
  selected: boolean
  recommended: boolean
  onClick: () => void
}

const BAND_LABELS: Record<string, string> = {
  high: 'HIGH CONFIDENCE',
  good: 'GOOD',
  review: 'REVIEW ADVISED',
  do_not_use: 'DO NOT USE',
}

const BAND_ICON = {
  high:       <CheckCircle2 className="h-3 w-3" />,
  good:       <CheckCircle2 className="h-3 w-3" />,
  review:     <AlertTriangle className="h-3 w-3" />,
  do_not_use: <XCircle className="h-3 w-3" />,
}

const VARIANT_COLORS: Record<string, string> = {
  A: 'border-green/30 bg-green/[0.03]',
  B: 'border-blue/30 bg-blue/[0.03]',
  C: 'border-amber/30 bg-amber/[0.03]',
}

const VARIANT_ACCENT: Record<string, string> = {
  A: 'text-green',
  B: 'text-blue',
  C: 'text-amber',
}

export function VariantCard({ variant, selected, recommended, onClick }: Props) {
  const { spec, analytical_stress_mpa, mass_kg, cost_usd, safety_factor, dfm_issues, triple_lock, sandbox } = variant
  const band = triple_lock?.band ?? null
  const sf = safety_factor

  return (
    <motion.button
      layout
      onClick={onClick}
      whileHover={{ y: -1 }}
      whileTap={{ y: 0 }}
      className={cn(
        'relative w-full text-left border transition-all duration-150 cursor-pointer',
        'focus:outline-none focus-visible:ring-1 focus-visible:ring-blue',
        selected
          ? cn('border-blue bg-blue-glow shadow-glow-blue', VARIANT_COLORS[spec.label])
          : cn('border-border hover:border-border-strong bg-bg-2', VARIANT_COLORS[spec.label]),
        !sandbox.ok && 'opacity-50',
      )}
    >
      {/* Selected indicator strip */}
      {selected && (
        <motion.div
          layoutId="variant-selected"
          className="absolute left-0 top-0 bottom-0 w-0.5 bg-blue"
        />
      )}

      {/* Recommended badge */}
      {recommended && (
        <div className="absolute top-2 right-2 flex items-center gap-1 bg-blue/10 border border-blue/30 px-1.5 py-0.5">
          <Zap className="h-2.5 w-2.5 text-blue" />
          <span className="font-mono text-2xs text-blue">RECOMMENDED</span>
        </div>
      )}

      <div className="p-4">
        {/* Header */}
        <div className="flex items-baseline gap-2 mb-3">
          <span className={cn('font-mono text-lg font-medium', VARIANT_ACCENT[spec.label])}>
            {spec.label}
          </span>
          <span className="font-mono text-xs text-text-muted uppercase tracking-wider">
            {spec.name}
          </span>
        </div>

        {/* Rationale */}
        <p className="text-xs text-text-muted leading-relaxed mb-4 line-clamp-2">
          {spec.rationale}
        </p>

        {/* Engineering numbers grid */}
        <div className="grid grid-cols-2 gap-x-4 gap-y-3 mb-4">
          <Metric
            icon={<ShieldCheck className="h-3 w-3" />}
            label="Safety Factor"
            value={fmt(sf, 2, '×')}
            valueClass={sfColor(sf)}
          />
          <Metric
            icon={<Weight className="h-3 w-3" />}
            label="Mass"
            value={fmt(mass_kg, 3, 'kg')}
            valueClass="eng-number-neutral"
          />
          <Metric
            icon={<DollarSign className="h-3 w-3" />}
            label="Est. Cost"
            value={cost_usd != null ? `$${cost_usd.toFixed(0)}` : '—'}
            valueClass="eng-number-neutral"
          />
          <Metric
            icon={<Zap className="h-3 w-3" />}
            label="Max Stress"
            value={fmt(analytical_stress_mpa, 1, 'MPa')}
            valueClass="eng-number"
          />
        </div>

        {/* Dimensions summary */}
        <div className="border-t border-border pt-3 mb-3">
          <p className="label-xs mb-1.5">Dimensions</p>
          <div className="font-mono text-2xs text-text-muted space-y-0.5">
            <div className="flex justify-between">
              <span>Base</span>
              <span className="text-text">{spec.base_width_mm}×{spec.base_depth_mm}×{spec.base_thickness_mm} mm</span>
            </div>
            <div className="flex justify-between">
              <span>Wall</span>
              <span className="text-text">{spec.wall_height_mm}×{spec.wall_thickness_mm} mm</span>
            </div>
            <div className="flex justify-between">
              <span>Fillet R</span>
              <span className="text-text">{spec.fillet_radius_mm} mm</span>
            </div>
          </div>
        </div>

        {/* Triple-Lock confidence */}
        {triple_lock && (
          <div className={cn('flex items-center gap-1.5 mb-3', bandColor(band))}>
            {BAND_ICON[band ?? 'review']}
            <span className="font-mono text-2xs">
              {BAND_LABELS[band ?? 'review']} · {(triple_lock.score * 100).toFixed(0)}%
            </span>
          </div>
        )}

        {/* DFM issues */}
        {dfm_issues.length > 0 && (
          <div className="border-t border-border pt-3">
            <p className="label-xs mb-1.5 text-amber">DFM Issues ({dfm_issues.length})</p>
            <ul className="space-y-0.5">
              {dfm_issues.slice(0, 2).map((issue, i) => (
                <li key={i} className="font-mono text-2xs text-amber/80 leading-relaxed">
                  · {issue}
                </li>
              ))}
              {dfm_issues.length > 2 && (
                <li className="font-mono text-2xs text-text-faint">
                  +{dfm_issues.length - 2} more
                </li>
              )}
            </ul>
          </div>
        )}

        {/* Sandbox failure */}
        {!sandbox.ok && (
          <div className="border-t border-red/30 pt-3 mt-3">
            <p className="font-mono text-2xs text-red">
              ✗ Generation failed: {sandbox.stage}
            </p>
          </div>
        )}
      </div>
    </motion.button>
  )
}

function Metric({
  icon,
  label,
  value,
  valueClass,
}: {
  icon: React.ReactNode
  label: string
  value: string
  valueClass: string
}) {
  return (
    <div>
      <div className="flex items-center gap-1 mb-0.5">
        <span className="text-text-faint">{icon}</span>
        <span className="label-xs">{label}</span>
      </div>
      <span className={cn('font-mono text-sm', valueClass)}>{value}</span>
    </div>
  )
}
