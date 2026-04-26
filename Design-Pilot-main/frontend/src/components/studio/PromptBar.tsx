import { useRef, type KeyboardEvent } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { ArrowRight, Loader2, AlertCircle } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useStudio } from '@/store'

interface Props {
  onSubmit: (prompt: string) => void
}

export function PromptBar({ onSubmit }: Props) {
  const { prompt, setPrompt, status, streamError } = useStudio()
  const ref = useRef<HTMLTextAreaElement>(null)
  const isStreaming = status === 'streaming'
  const hasError = status === 'error'

  function handleKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (prompt.trim().length >= 10 && !isStreaming) onSubmit(prompt.trim())
    }
  }

  function handleSubmit() {
    if (prompt.trim().length >= 10 && !isStreaming) onSubmit(prompt.trim())
  }

  const valid = prompt.trim().length >= 10

  return (
    <div className="w-full">
      <div
        className={cn(
          'relative border transition-colors duration-150',
          isStreaming
            ? 'border-blue/60 bg-blue-glow/50'
            : hasError
            ? 'border-red/60 bg-bg-2'
            : 'border-border bg-bg-2 hover:border-border-strong focus-within:border-blue',
        )}
      >
        {/* Streaming indicator strip */}
        <AnimatePresence>
          {isStreaming && (
            <motion.div
              className="absolute top-0 left-0 h-0.5 progress-shimmer"
              initial={{ width: '0%' }}
              animate={{ width: `${useStudio.getState().progressPct}%` }}
              transition={{ duration: 0.4, ease: 'easeOut' }}
            />
          )}
        </AnimatePresence>

        <textarea
          ref={ref}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={handleKey}
          placeholder={
            isStreaming
              ? 'Generating…'
              : 'Describe your bracket — e.g. "Aluminum L-bracket for 50 kg static load, 100 mm arm, M8 bolts, CNC"'
          }
          disabled={isStreaming}
          rows={3}
          className={cn(
            'w-full resize-none bg-transparent px-4 pt-4 pb-10 font-mono text-sm',
            'text-text placeholder:text-text-faint outline-none',
            'disabled:opacity-60 disabled:cursor-not-allowed',
          )}
        />

        {/* Bottom row: char count + submit */}
        <div className="absolute bottom-0 left-0 right-0 flex items-center justify-between px-4 pb-3">
          <span className="font-mono text-2xs text-text-faint">
            {prompt.length}/4000
            {prompt.length > 0 && prompt.length < 10 && (
              <span className="ml-2 text-amber">min 10 chars</span>
            )}
          </span>

          <div className="flex items-center gap-2">
            <span className="font-mono text-2xs text-text-faint hidden sm:block">
              ⏎ to run · ⇧⏎ newline
            </span>
            <button
              onClick={handleSubmit}
              disabled={!valid || isStreaming}
              className={cn(
                'flex items-center gap-1.5 px-3 py-1.5 font-mono text-xs transition-all duration-150',
                'border',
                valid && !isStreaming
                  ? 'border-blue bg-blue text-white hover:bg-blue-dim cursor-pointer'
                  : 'border-border text-text-faint cursor-not-allowed',
              )}
            >
              {isStreaming ? (
                <><Loader2 className="h-3 w-3 animate-spin" /> Generating</>
              ) : (
                <><ArrowRight className="h-3 w-3" /> Generate</>
              )}
            </button>
          </div>
        </div>
      </div>

      {/* Error banner */}
      <AnimatePresence>
        {hasError && streamError && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            className="flex items-start gap-2 border border-red/40 bg-red/5 px-4 py-2 mt-px"
          >
            <AlertCircle className="h-3 w-3 mt-0.5 flex-shrink-0 text-red" />
            <span className="font-mono text-2xs text-red">{streamError}</span>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
