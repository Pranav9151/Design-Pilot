import { motion, AnimatePresence } from 'framer-motion'
import { CheckCircle2, Circle, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'

const STAGES = [
  { key: 'loading',             label: 'Loading materials' },
  { key: 'parsing_prompt',      label: 'Parsing prompt' },
  { key: 'deriving_variants',   label: 'Deriving A/B/C variants' },
  { key: 'variant_a',          label: 'Building variant A (Lightest)' },
  { key: 'variant_b',          label: 'Building variant B (Strongest)' },
  { key: 'variant_c',          label: 'Building variant C (Economical)' },
  { key: 'qa_synthesis',        label: 'Engineering QA synthesis' },
  { key: 'saving',              label: 'Persisting design' },
  { key: 'done',                label: 'Complete' },
]

function stageIndex(stage: string): number {
  const normalized = stage.replace(/_start$|_sandbox$|_done$/, '').replace('variant_a_', 'variant_a').replace('variant_b_', 'variant_b').replace('variant_c_', 'variant_c')
  const idx = STAGES.findIndex(s => normalized.startsWith(s.key))
  return idx === -1 ? 0 : idx
}

interface Props {
  stage: string
  message: string
  pct: number
  visible: boolean
}

export function ProgressStream({ stage, message, pct, visible }: Props) {
  const currentIdx = stageIndex(stage)

  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -4 }}
          transition={{ duration: 0.2 }}
          className="border border-border bg-bg-2 p-5"
        >
          {/* Overall progress bar */}
          <div className="mb-5">
            <div className="flex items-center justify-between mb-1.5">
              <span className="label-xs">GENERATING</span>
              <span className="font-mono text-2xs text-blue">{pct}%</span>
            </div>
            <div className="h-0.5 bg-bg-4 overflow-hidden">
              <motion.div
                className="h-full progress-shimmer"
                initial={{ width: '0%' }}
                animate={{ width: `${pct}%` }}
                transition={{ duration: 0.5, ease: 'easeOut' }}
              />
            </div>
          </div>

          {/* Stage list */}
          <div className="space-y-2">
            {STAGES.map((s, idx) => {
              const done    = idx < currentIdx
              const active  = idx === currentIdx
              const pending = idx > currentIdx

              return (
                <motion.div
                  key={s.key}
                  className={cn(
                    'flex items-center gap-2.5 font-mono text-xs transition-colors duration-200',
                    done    && 'text-text-muted',
                    active  && 'text-text',
                    pending && 'text-text-faint',
                  )}
                >
                  {done ? (
                    <CheckCircle2 className="h-3 w-3 flex-shrink-0 text-green" />
                  ) : active ? (
                    <Loader2 className="h-3 w-3 flex-shrink-0 text-blue animate-spin" />
                  ) : (
                    <Circle className="h-3 w-3 flex-shrink-0 text-text-faint" />
                  )}
                  <span>{s.label}</span>
                </motion.div>
              )
            })}
          </div>

          {/* Current message */}
          {message && (
            <motion.p
              key={message}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="mt-4 font-mono text-2xs text-text-muted border-t border-border pt-3"
            >
              {message}
            </motion.p>
          )}
        </motion.div>
      )}
    </AnimatePresence>
  )
}
