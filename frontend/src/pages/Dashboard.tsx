import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import {
  Plus, Trash2, Download, ChevronRight,
  CheckCircle2, AlertTriangle, XCircle, Clock,
} from 'lucide-react'
import { fetchDesigns, deleteDesign } from '@/lib/api'
import { useAuth } from '@/store'
import { cn, relativeTime, bandColor } from '@/lib/utils'
import { toast, Toaster } from 'sonner'
import type { DesignSummary } from '@/lib/api'

const STATUS_ICON: Record<string, React.ReactNode> = {
  analyzed:  <CheckCircle2 className="h-3 w-3 text-green" />,
  generated: <Clock className="h-3 w-3 text-blue" />,
  failed:    <XCircle className="h-3 w-3 text-red" />,
  archived:  <AlertTriangle className="h-3 w-3 text-text-faint" />,
  draft:     <Clock className="h-3 w-3 text-text-faint" />,
}

export function DashboardPage() {
  const { token } = useAuth()
  const qc = useQueryClient()
  const navigate = useNavigate()

  const { data: designs = [], isLoading, isError } = useQuery({
    queryKey: ['designs'],
    queryFn: () => fetchDesigns(token()!),
    enabled: !!token(),
    staleTime: 30_000,
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteDesign(token()!, id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['designs'] })
      toast.success('Design archived')
    },
    onError: () => toast.error('Failed to archive design'),
  })

  return (
    <div className="min-h-screen bg-bg pt-12">
      <Toaster
        theme="dark"
        position="top-right"
        toastOptions={{
          style: { background: '#161719', border: '1px solid #2A2D31', color: '#E8EAED', fontFamily: 'IBM Plex Mono, monospace', fontSize: '12px' },
        }}
      />

      <div className="max-w-5xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="font-mono text-lg font-medium text-text">Designs</h1>
            <p className="font-mono text-xs text-text-faint mt-1">
              {designs.filter(d => d.status !== 'archived').length} active
            </p>
          </div>
          <Link
            to="/studio"
            className="flex items-center gap-2 px-4 py-2 bg-blue text-white font-mono text-xs hover:bg-blue-dim transition-colors"
          >
            <Plus className="h-3.5 w-3.5" />
            New Design
          </Link>
        </div>

        {/* Loading */}
        {isLoading && (
          <div className="space-y-2">
            {[1, 2, 3].map(i => (
              <div key={i} className="h-20 bg-bg-2 border border-border animate-pulse" />
            ))}
          </div>
        )}

        {/* Error */}
        {isError && (
          <div className="border border-red/40 bg-red/5 px-4 py-6 text-center">
            <p className="font-mono text-xs text-red">Failed to load designs. Check your connection.</p>
          </div>
        )}

        {/* Empty */}
        {!isLoading && designs.length === 0 && (
          <div className="border border-border bg-bg-2 px-6 py-16 text-center">
            <p className="font-mono text-sm text-text-muted mb-2">No designs yet</p>
            <p className="font-mono text-xs text-text-faint mb-6">
              Open Studio and describe your first bracket.
            </p>
            <Link
              to="/studio"
              className="inline-flex items-center gap-2 px-4 py-2 border border-blue text-blue font-mono text-xs hover:bg-blue/10 transition-colors"
            >
              <Plus className="h-3.5 w-3.5" /> Open Studio
            </Link>
          </div>
        )}

        {/* Table header */}
        {!isLoading && designs.length > 0 && (
          <div>
            <div className="grid grid-cols-[auto_1fr_120px_100px_80px_80px] gap-4 items-center px-4 py-2 mb-1">
              <span className="label-xs w-5"></span>
              <span className="label-xs">PROMPT</span>
              <span className="label-xs text-right">CONFIDENCE</span>
              <span className="label-xs text-right">VARIANT</span>
              <span className="label-xs text-right">CREATED</span>
              <span className="label-xs"></span>
            </div>

            <div className="space-y-1">
              {designs.map((d, i) => (
                <DesignRow
                  key={d.id}
                  design={d}
                  index={i}
                  onDelete={() => {
                    if (confirm('Archive this design?')) deleteMutation.mutate(d.id)
                  }}
                  onOpen={() => navigate(`/studio/${d.id}`)}
                />
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function DesignRow({
  design: d,
  index,
  onDelete,
  onOpen,
}: {
  design: DesignSummary
  index: number
  onDelete: () => void
  onOpen: () => void
}) {
  const archived = d.status === 'archived'

  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.04, duration: 0.2 }}
      className={cn(
        'group grid grid-cols-[auto_1fr_120px_100px_80px_80px] gap-4 items-center',
        'px-4 py-3 border border-border bg-bg-2 hover:bg-bg-3 hover:border-border-strong transition-colors',
        archived && 'opacity-40',
      )}
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onOpen()
        }
      }}
    >
      {/* Status icon */}
      <div className="w-5 flex justify-center">
        {STATUS_ICON[d.status] ?? <Clock className="h-3 w-3 text-text-faint" />}
      </div>

      {/* Prompt */}
      <div className="min-w-0">
        <p className="font-mono text-xs text-text truncate">
          {d.prompt ?? d.name ?? '(untitled)'}
        </p>
        <p className="font-mono text-2xs text-text-faint mt-0.5 capitalize">
          {d.status}
        </p>
      </div>

      {/* Confidence */}
      <div className="text-right">
        {d.confidence_band ? (
          <span className={cn('font-mono text-xs', bandColor(d.confidence_band))}>
            {d.confidence_band}
          </span>
        ) : (
          <span className="font-mono text-xs text-text-faint">—</span>
        )}
      </div>

      {/* Recommended variant */}
      <div className="text-right">
        <span className="font-mono text-xs text-text-muted">
          {d.recommended_variant ? `Variant ${d.recommended_variant}` : '—'}
        </span>
      </div>

      {/* Time */}
      <div className="text-right">
        <span className="font-mono text-2xs text-text-faint">
          {relativeTime(d.created_at)}
        </span>
      </div>

      {/* Actions */}
      <div className="flex items-center justify-end gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        {d.step_url && (
          <a
            href={d.step_url}
            target="_blank"
            rel="noopener noreferrer"
            title="Download STEP"
            className="p-1.5 text-text-faint hover:text-text transition-colors"
            onClick={e => e.stopPropagation()}
          >
            <Download className="h-3.5 w-3.5" />
          </a>
        )}
        {!archived && (
          <button
            onClick={(e) => { e.stopPropagation(); onDelete() }}
            title="Archive"
            className="p-1.5 text-text-faint hover:text-red transition-colors"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        )}
        <ChevronRight className="h-3.5 w-3.5 text-text-faint" />
      </div>
    </motion.div>
  )
}
