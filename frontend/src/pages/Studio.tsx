import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'
import {
  AlertCircle,
  BookOpen,
  CheckCircle2,
  Download,
  FileCode2,
  Lightbulb,
  RotateCcw,
  Share2,
  Sparkles,
} from 'lucide-react'
import { Toaster, toast } from 'sonner'

import type {
  DesignDetail,
  DiaryEntry,
  MaterialRecommendationItem,
  ParameterPatchRequest,
  SimilarDesignItem,
  WhyNotResponse,
} from '@/lib/api'
import {
  explainDesign,
  fetchDesign,
  fetchDesignDiary,
  fetchDesignQuestions,
  fetchSimilarDesigns,
  fetchWhyNot,
  optimizeDesign,
  patchDesignParameters,
  recommendMaterials,
  streamDesign,
} from '@/lib/api'
import { bandColor, cn, fmt, relativeTime, sfColor } from '@/lib/utils'
import { useAuth, useStudio } from '@/store'

import { ModelViewer } from '@/components/studio/ModelViewer'
import { ProgressStream } from '@/components/studio/ProgressStream'
import { PromptBar } from '@/components/studio/PromptBar'
import { VariantCard } from '@/components/studio/VariantCard'

type VariantLabel = 'A' | 'B' | 'C'

type EditableSpecKey =
  | 'base_width_mm'
  | 'base_depth_mm'
  | 'base_thickness_mm'
  | 'wall_height_mm'
  | 'wall_thickness_mm'
  | 'fillet_radius_mm'
  | 'hole_diameter_mm'
  | 'hole_spacing_x_mm'
  | 'hole_spacing_y_mm'

type EditableSpecState = Record<EditableSpecKey, number>

const EDITABLE_FIELDS: Array<{
  key: EditableSpecKey
  label: string
  min: number
  max: number
  step: number
}> = [
  { key: 'base_width_mm', label: 'Base width', min: 20, max: 300, step: 1 },
  { key: 'base_depth_mm', label: 'Base depth', min: 20, max: 300, step: 1 },
  { key: 'base_thickness_mm', label: 'Base thickness', min: 2, max: 30, step: 0.5 },
  { key: 'wall_height_mm', label: 'Wall height', min: 20, max: 300, step: 1 },
  { key: 'wall_thickness_mm', label: 'Wall thickness', min: 2, max: 30, step: 0.5 },
  { key: 'fillet_radius_mm', label: 'Fillet radius', min: 1, max: 25, step: 0.5 },
  { key: 'hole_diameter_mm', label: 'Hole diameter', min: 2, max: 40, step: 0.5 },
  { key: 'hole_spacing_x_mm', label: 'Hole spacing X', min: 5, max: 200, step: 1 },
  { key: 'hole_spacing_y_mm', label: 'Hole spacing Y', min: 5, max: 200, step: 1 },
]

const DEFAULT_OPTIMIZE_GOAL = 'Reduce mass while keeping a strong safety margin.'

export function StudioPage() {
  const { token } = useAuth()
  const studio = useStudio()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const abortRef = useRef<AbortController | null>(null)
  const { id } = useParams<{ id: string }>()

  const [parameterDraftState, setParameterDraftState] = useState<{
    sourceKey: string
    values: EditableSpecState
  } | null>(null)
  const [optimizeGoal, setOptimizeGoal] = useState(DEFAULT_OPTIMIZE_GOAL)

  const authToken = token()
  const designQuery = useQuery({
    queryKey: ['design', id],
    queryFn: () => fetchDesign(authToken!, id!),
    enabled: !!id && !!authToken,
    staleTime: 30_000,
  })

  useEffect(() => {
    if (designQuery.data && id) {
      studio.setComplete(designQuery.data)
    }
  }, [designQuery.data, id, studio])

  const handleGenerate = useCallback((prompt: string) => {
    if (!authToken) {
      toast.error('Not authenticated')
      return
    }

    abortRef.current?.abort()
    studio.startGeneration()

    abortRef.current = streamDesign(authToken, prompt, {
      onProgress: (event) => studio.setProgress(event),
      onComplete: (design) => {
        studio.setComplete(design)
        queryClient.invalidateQueries({ queryKey: ['designs'] })
        toast.success('Design ready: 3 variants generated')
      },
      onError: (code, message) => {
        studio.setError(message)
        if (code === '429') {
          toast.error('Monthly design limit reached. Upgrade to Pro for more.')
        } else {
          toast.error(`Generation failed: ${message}`)
        }
      },
    })
  }, [authToken, queryClient, studio])

  const activeDesign = studio.activeDesign
  const activeDesignId = activeDesign?.id ?? null
  const selectedLabel = studio.selectedVariant
  const variants = activeDesign?.parameters?.variants ?? []
  const selectedVariant =
    variants.find((variant) => variant.spec.label === selectedLabel) ??
    variants[0] ??
    null
  const recommended = (activeDesign?.parameters?.recommended ?? 'C') as VariantLabel
  const isStreaming = studio.status === 'streaming'
  const isDone = studio.status === 'done'

  const selectedVariantDraftKey = `${activeDesignId ?? 'new'}:${selectedLabel}:${selectedVariant?.glb_url ?? 'placeholder'}`
  const baseParameterDraft = useMemo<EditableSpecState | null>(() => {
    if (!selectedVariant) return null
    return {
      base_width_mm: selectedVariant.spec.base_width_mm,
      base_depth_mm: selectedVariant.spec.base_depth_mm,
      base_thickness_mm: selectedVariant.spec.base_thickness_mm,
      wall_height_mm: selectedVariant.spec.wall_height_mm,
      wall_thickness_mm: selectedVariant.spec.wall_thickness_mm,
      fillet_radius_mm: selectedVariant.spec.fillet_radius_mm,
      hole_diameter_mm: selectedVariant.spec.hole_diameter_mm,
      hole_spacing_x_mm: selectedVariant.spec.hole_spacing_x_mm,
      hole_spacing_y_mm: selectedVariant.spec.hole_spacing_y_mm,
    }
  }, [selectedVariant])

  const parameterDraft =
    parameterDraftState?.sourceKey === selectedVariantDraftKey
      ? parameterDraftState.values
      : baseParameterDraft

  const diaryQuery = useQuery({
    queryKey: ['design-diary', activeDesignId],
    queryFn: () => fetchDesignDiary(authToken!, activeDesignId!),
    enabled: !!authToken && !!activeDesignId,
    staleTime: 15_000,
  })

  const whyNotQuery = useQuery({
    queryKey: ['design-why-not', activeDesignId],
    queryFn: () => fetchWhyNot(authToken!, activeDesignId!),
    enabled: !!authToken && !!activeDesignId,
    staleTime: 60_000,
  })

  const similarQuery = useQuery({
    queryKey: ['design-similar', activeDesignId],
    queryFn: () => fetchSimilarDesigns(authToken!, activeDesignId!, 4),
    enabled: !!authToken && !!activeDesignId,
    staleTime: 60_000,
  })

  const questionsQuery = useQuery({
    queryKey: ['design-questions', activeDesignId],
    queryFn: () => fetchDesignQuestions(authToken!, activeDesignId!),
    enabled: !!authToken && !!activeDesignId,
    staleTime: 60_000,
  })

  const materialRecommendationsQuery = useQuery({
    queryKey: ['material-recommendations', activeDesignId],
    queryFn: () =>
      recommendMaterials(authToken!, {
        load_n: Number(activeDesign?.parameters?.request?.load?.magnitude_n ?? 1000),
        process: normalizeProcess(activeDesign?.parameters?.request?.process),
        environment: 'indoor',
        prioritize: 'balanced',
        top_k: 3,
      }),
    enabled: !!authToken && !!activeDesignId,
    staleTime: 60_000,
  })

  const explainMutation = useMutation({
    mutationFn: () => explainDesign(authToken!, activeDesignId!),
    onSuccess: async (result) => {
      await navigator.clipboard.writeText(result.summary)
      toast.success('Manager summary copied to clipboard')
    },
    onError: () => toast.error('Failed to build manager summary'),
  })

  const parameterMutation = useMutation({
    mutationFn: (payload: ParameterPatchRequest) =>
      patchDesignParameters(authToken!, activeDesignId!, payload),
    onSuccess: (design) => {
      studio.setComplete(design)
      queryClient.invalidateQueries({ queryKey: ['designs'] })
      queryClient.invalidateQueries({ queryKey: ['design', activeDesignId] })
      queryClient.invalidateQueries({ queryKey: ['design-diary', activeDesignId] })
      queryClient.invalidateQueries({ queryKey: ['design-why-not', activeDesignId] })
      queryClient.invalidateQueries({ queryKey: ['design-questions', activeDesignId] })
      toast.success('Parameters updated and design re-analyzed')
    },
    onError: () => toast.error('Parameter update failed'),
  })

  const optimizeMutation = useMutation({
    mutationFn: () => optimizeDesign(authToken!, activeDesignId!, { goal: optimizeGoal }),
    onSuccess: (result) => {
      studio.setComplete(result.design)
      queryClient.invalidateQueries({ queryKey: ['designs'] })
      queryClient.invalidateQueries({ queryKey: ['design', activeDesignId] })
      queryClient.invalidateQueries({ queryKey: ['design-diary', activeDesignId] })
      queryClient.invalidateQueries({ queryKey: ['design-why-not', activeDesignId] })
      toast.success(`Optimization complete. Variant ${result.recommended_variant} is now recommended.`)
    },
    onError: () => toast.error('Optimization request failed'),
  })

  const hasParameterChanges = useMemo(() => {
    if (!selectedVariant || !parameterDraft) return false
    return EDITABLE_FIELDS.some(({ key }) => parameterDraft[key] !== selectedVariant.spec[key])
  }, [parameterDraft, selectedVariant])

  const applyParameterChanges = useCallback(() => {
    if (!selectedVariant || !parameterDraft) return

    const payload = EDITABLE_FIELDS.reduce<ParameterPatchRequest>((acc, field) => {
      if (parameterDraft[field.key] !== selectedVariant.spec[field.key]) {
        acc[field.key] = Number(parameterDraft[field.key])
      }
      return acc
    }, {})

    if (Object.keys(payload).length === 0) {
      toast.message('No parameter changes to apply')
      return
    }

    payload.recommended_variant = selectedLabel
    parameterMutation.mutate(payload)
  }, [parameterDraft, parameterMutation, selectedLabel, selectedVariant])

  const resetParameterDraft = useCallback(() => {
    if (!baseParameterDraft) return
    setParameterDraftState({
      sourceKey: selectedVariantDraftKey,
      values: baseParameterDraft,
    })
  }, [baseParameterDraft, selectedVariantDraftKey])

  return (
    <div className="min-h-screen bg-bg pt-12">
      <Toaster
        theme="dark"
        position="top-right"
        toastOptions={{
          style: {
            background: '#161719',
            border: '1px solid #2A2D31',
            color: '#E8EAED',
            fontFamily: 'IBM Plex Mono, monospace',
            fontSize: '12px',
          },
        }}
      />

      <div className="flex h-[calc(100vh-48px)]">
        <div className="w-[520px] flex-shrink-0 flex flex-col border-r border-border overflow-y-auto">
          <div className="p-5 border-b border-border">
            <div className="flex items-center justify-between mb-3">
              <span className="label-xs">PROMPT</span>
              {isDone && (
                <button
                  onClick={() => studio.resetGeneration()}
                  className="flex items-center gap-1 font-mono text-2xs text-text-muted hover:text-text transition-colors"
                >
                  <RotateCcw className="h-3 w-3" />
                  New design
                </button>
              )}
            </div>
            <PromptBar onSubmit={handleGenerate} />
          </div>

          <AnimatePresence>
            {isStreaming && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: 'auto' }}
                exit={{ opacity: 0, height: 0 }}
                className="border-b border-border overflow-hidden"
              >
                <div className="p-5">
                  <ProgressStream
                    stage={studio.progressStage}
                    message={studio.progressMessage}
                    pct={studio.progressPct}
                    visible
                  />
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          <AnimatePresence>
            {isDone && variants.length > 0 && (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ duration: 0.3 }}
                className="p-5 border-b border-border"
              >
                <div className="flex items-center justify-between mb-3">
                  <span className="label-xs">3 VARIANTS</span>
                  <span className="font-mono text-2xs text-text-faint">click to inspect</span>
                </div>
                <div className="space-y-2">
                  {variants.map((variant) => (
                    <VariantCard
                      key={variant.spec.label}
                      variant={variant}
                      selected={variant.spec.label === selectedLabel}
                      recommended={variant.spec.label === recommended}
                      onClick={() => studio.setSelectedVariant(variant.spec.label as VariantLabel)}
                    />
                  ))}
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          <AnimatePresence>
            {isDone && selectedVariant && (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 0.1 }}
                className="p-5 space-y-4 border-b border-border"
              >
                <QaSummary design={activeDesign!} selectedLabel={selectedLabel} />

                <PanelSection
                  title="ACTION COPILOT"
                  action={activeDesignId ? (
                    <button
                      onClick={() => explainMutation.mutate()}
                      disabled={explainMutation.isPending}
                      className="font-mono text-2xs text-blue hover:text-blue-dim disabled:opacity-50"
                    >
                      {explainMutation.isPending ? 'building...' : 'copy manager summary'}
                    </button>
                  ) : null}
                >
                  <button
                    onClick={() => optimizeMutation.mutate()}
                    disabled={!activeDesignId || optimizeMutation.isPending}
                    className="flex items-center gap-2 w-full px-3 py-2 border border-blue/50 bg-blue/10 text-blue font-mono text-xs hover:bg-blue/20 transition-colors disabled:opacity-50"
                  >
                    <Sparkles className="h-3.5 w-3.5" />
                    {optimizeMutation.isPending ? 'Optimizing current design...' : 'Auto-optimize current design'}
                  </button>
                  <textarea
                    value={optimizeGoal}
                    onChange={(event) => setOptimizeGoal(event.target.value)}
                    className="w-full min-h-20 px-3 py-2 bg-bg-2 border border-border text-text font-mono text-xs outline-none focus:border-border-strong resize-y"
                    placeholder="Describe the optimization goal"
                  />
                </PanelSection>

                <PanelSection
                  title="PARAMETER TUNING"
                  action={(
                    <div className="flex items-center gap-3">
                      <button
                        onClick={resetParameterDraft}
                        disabled={!hasParameterChanges}
                        className="font-mono text-2xs text-text-faint hover:text-text disabled:opacity-40"
                      >
                        reset
                      </button>
                      <button
                        onClick={applyParameterChanges}
                        disabled={!hasParameterChanges || parameterMutation.isPending}
                        className="font-mono text-2xs text-blue hover:text-blue-dim disabled:opacity-40"
                      >
                        {parameterMutation.isPending ? 'rebuilding...' : 'apply + reanalyze'}
                      </button>
                    </div>
                  )}
                >
                  {parameterDraft && (
                    <div className="space-y-3">
                      {EDITABLE_FIELDS.map((field) => (
                        <ParameterField
                          key={field.key}
                          field={field}
                          value={parameterDraft[field.key]}
                          onChange={(value) =>
                            setParameterDraftState((current) => ({
                              sourceKey: selectedVariantDraftKey,
                              values: {
                                ...(current?.sourceKey === selectedVariantDraftKey ? current.values : parameterDraft!),
                                [field.key]: value,
                              },
                            }))
                          }
                        />
                      ))}
                    </div>
                  )}
                </PanelSection>

                <PanelSection title="WHY THIS VARIANT">
                  <WhyNotPanel whyNot={whyNotQuery.data} loading={whyNotQuery.isLoading} />
                </PanelSection>

                <PanelSection title="ENGINEERING QUESTIONS">
                  {questionsQuery.isLoading && (
                    <p className="font-mono text-2xs text-text-faint">Preparing review questions...</p>
                  )}
                  {questionsQuery.data?.questions?.length ? (
                    <ul className="space-y-2">
                      {questionsQuery.data.questions.map((question) => (
                        <li key={question} className="font-mono text-2xs text-text-muted leading-relaxed">
                          - {question}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    !questionsQuery.isLoading && (
                      <p className="font-mono text-2xs text-text-faint">No review questions generated yet.</p>
                    )
                  )}
                </PanelSection>

                <PanelSection title="DESIGN DIARY">
                  <DiaryPanel entries={diaryQuery.data ?? []} loading={diaryQuery.isLoading} />
                </PanelSection>

                <PanelSection title="SIMILAR DESIGNS">
                  <SimilarDesignsPanel
                    items={similarQuery.data?.items ?? []}
                    loading={similarQuery.isLoading}
                    onOpen={(designId) => navigate(`/studio/${designId}`)}
                  />
                </PanelSection>

                <PanelSection title="MATERIAL SCOUT">
                  <MaterialRecommendationsPanel
                    items={materialRecommendationsQuery.data?.items ?? []}
                    loading={materialRecommendationsQuery.isLoading}
                  />
                </PanelSection>
              </motion.div>
            )}
          </AnimatePresence>

          <AnimatePresence>
            {isDone && selectedVariant && (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 0.15 }}
                className="p-5 space-y-2"
              >
                <span className="label-xs block mb-3">EXPORT</span>

                {selectedVariant.step_url ? (
                  <a
                    href={selectedVariant.step_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-2 w-full px-4 py-2.5 border border-blue bg-blue/10 text-blue font-mono text-xs hover:bg-blue/20 transition-colors"
                  >
                    <Download className="h-3.5 w-3.5" />
                    Download STEP for Variant {selectedLabel}
                  </a>
                ) : (
                  <div className="flex items-center gap-2 w-full px-4 py-2.5 border border-border text-text-faint font-mono text-xs">
                    <AlertCircle className="h-3.5 w-3.5" />
                    STEP not available for this variant
                  </div>
                )}

                <button
                  onClick={() => {
                    navigator.clipboard.writeText(selectedVariant.cadquery_code)
                    toast.success('CadQuery code copied')
                  }}
                  className="flex items-center gap-2 w-full px-4 py-2.5 border border-border text-text-muted font-mono text-xs hover:border-border-strong hover:text-text transition-colors"
                >
                  <FileCode2 className="h-3.5 w-3.5" />
                  Copy CadQuery source
                </button>

                <button
                  onClick={() => {
                    const shareUrl = `${window.location.origin}/studio/${activeDesignId}`
                    navigator.clipboard.writeText(shareUrl)
                    toast.success('Studio link copied')
                  }}
                  className="flex items-center gap-2 w-full px-4 py-2.5 border border-border text-text-muted font-mono text-xs hover:border-border-strong hover:text-text transition-colors"
                >
                  <Share2 className="h-3.5 w-3.5" />
                  Copy studio link
                </button>
              </motion.div>
            )}
          </AnimatePresence>

          {studio.status === 'idle' && !designQuery.isLoading && (
            <div className="flex-1 flex flex-col items-center justify-center p-8 text-center">
              <BookOpen className="h-8 w-8 text-text-faint mb-4" />
              <p className="font-mono text-xs text-text-muted max-w-xs leading-relaxed">
                Describe your bracket above. Include load, material, and process.
              </p>
              <p className="font-mono text-2xs text-text-faint mt-3">
                Example: &quot;6061-T6 aluminium L-bracket, 50 kg static load, 100 mm arm, CNC, M8 bolts&quot;
              </p>
            </div>
          )}

          {designQuery.isLoading && (
            <div className="flex-1 flex items-center justify-center p-8">
              <p className="font-mono text-xs text-text-muted">Loading design...</p>
            </div>
          )}

          {designQuery.isError && (
            <div className="p-6 border-t border-border">
              <p className="font-mono text-xs text-red">Failed to load the saved design.</p>
            </div>
          )}
        </div>

        <div className="flex-1 flex flex-col">
          <div className="flex-1 relative">
            <ModelViewer
              key={selectedVariant?.glb_url ?? 'placeholder'}
              glbUrl={selectedVariant?.glb_url ?? null}
              className="w-full h-full"
            />

            <AnimatePresence>
              {isDone && selectedVariant?.triple_lock && (
                <motion.div
                  initial={{ opacity: 0, y: -8 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="absolute bottom-5 left-5 bg-bg-3/90 border border-border px-3 py-2 backdrop-blur-sm"
                >
                  <p className="label-xs mb-1">TRIPLE-LOCK</p>
                  <div className={cn('flex items-center gap-1.5', bandColor(selectedVariant.triple_lock.band))}>
                    <CheckCircle2 className="h-3 w-3" />
                    <span className="font-mono text-xs capitalize">
                      {selectedVariant.triple_lock.band.replace('_', ' ')}
                      {' · '}
                      {(selectedVariant.triple_lock.score * 100).toFixed(0)}% confidence
                    </span>
                  </div>
                  <p className="font-mono text-2xs text-text-faint mt-1 max-w-xs leading-relaxed">
                    {selectedVariant.triple_lock.explanation}
                  </p>
                </motion.div>
              )}
            </AnimatePresence>
          </div>

          <AnimatePresence>
            {isDone && selectedVariant && (
              <motion.div
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                className="h-14 border-t border-border bg-bg-1 flex items-center px-5 gap-8 flex-shrink-0 overflow-x-auto"
              >
                <MetricStrip label="VARIANT" value={`${selectedLabel} - ${selectedVariant.spec.name}`} className="text-text" />
                <MetricStrip label="SF" value={fmt(selectedVariant.safety_factor, 2, 'x')} className={sfColor(selectedVariant.safety_factor)} />
                <MetricStrip label="STRESS" value={fmt(selectedVariant.analytical_stress_mpa, 1, 'MPa')} className="eng-number" />
                <MetricStrip label="MASS" value={fmt(selectedVariant.mass_kg, 3, 'kg')} className="eng-number-neutral" />
                <MetricStrip label="EST. COST" value={selectedVariant.cost_usd != null ? `$${selectedVariant.cost_usd.toFixed(0)}` : '-'} className="eng-number-neutral" />
                {activeDesign?.simulation?.method && (
                  <MetricStrip label="METHOD" value="Shigley Eq. 3-24" className="text-text-faint" />
                )}
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </div>
  )
}

function MetricStrip({
  label,
  value,
  className,
}: {
  label: string
  value: string
  className?: string
}) {
  return (
    <div>
      <p className="label-xs mb-0.5">{label}</p>
      <p className={cn('font-mono text-sm', className)}>{value}</p>
    </div>
  )
}

function QaSummary({
  design,
  selectedLabel,
}: {
  design: DesignDetail
  selectedLabel: string
}) {
  const variant = design.parameters.variants.find((item) => item.spec.label === selectedLabel)
  if (!variant) return null

  const assumptions = design.assumptions ?? []
  const dfm = design.dfm?.issues ?? []

  return (
    <div className="space-y-4">
      <div>
        <span className="label-xs block mb-2">ANALYSIS</span>
        {design.confidence_explanation && (
          <p className="font-mono text-xs text-text-muted leading-relaxed">
            {design.confidence_explanation}
          </p>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <MiniMetric label="Safety factor" value={fmt(variant.safety_factor, 2, 'x')} tone={sfColor(variant.safety_factor)} />
        <MiniMetric label="Stress" value={fmt(variant.analytical_stress_mpa, 1, 'MPa')} />
        <MiniMetric label="Mass" value={fmt(variant.mass_kg, 3, 'kg')} />
        <MiniMetric label="Cost" value={variant.cost_usd != null ? `$${variant.cost_usd.toFixed(2)}` : '-'} />
      </div>

      {assumptions.length > 0 && (
        <div>
          <span className="label-xs block mb-2">ASSUMPTIONS</span>
          <ul className="space-y-1">
            {assumptions.map((assumption) => (
              <li key={assumption} className="font-mono text-2xs text-text-muted">
                - {assumption}
              </li>
            ))}
          </ul>
        </div>
      )}

      {dfm.length > 0 && (
        <div>
          <span className="label-xs block mb-2 text-amber">DFM FLAGS</span>
          <ul className="space-y-1">
            {dfm.map((issue) => (
              <li key={issue} className="font-mono text-2xs text-amber/80">
                - {issue}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function MiniMetric({
  label,
  value,
  tone,
}: {
  label: string
  value: string
  tone?: string
}) {
  return (
    <div className="border border-border bg-bg-2 px-3 py-2">
      <p className="label-xs mb-1">{label}</p>
      <p className={cn('font-mono text-xs text-text', tone)}>{value}</p>
    </div>
  )
}

function PanelSection({
  title,
  action,
  children,
}: {
  title: string
  action?: ReactNode
  children: ReactNode
}) {
  return (
    <section className="border border-border bg-bg-1/50 p-4 space-y-3">
      <div className="flex items-center justify-between gap-3">
        <span className="label-xs">{title}</span>
        {action}
      </div>
      {children}
    </section>
  )
}

function ParameterField({
  field,
  value,
  onChange,
}: {
  field: { key: string; label: string; min: number; max: number; step: number }
  value: number
  onChange: (value: number) => void
}) {
  return (
    <label className="block">
      <div className="flex items-center justify-between gap-3 mb-1">
        <span className="font-mono text-2xs text-text-muted">{field.label}</span>
        <span className="font-mono text-2xs text-text">{value.toFixed(field.step < 1 ? 1 : 0)} mm</span>
      </div>
      <div className="flex items-center gap-3">
        <input
          type="range"
          min={field.min}
          max={field.max}
          step={field.step}
          value={value}
          onChange={(event) => onChange(Number(event.target.value))}
          className="flex-1 accent-[#4D7CFE]"
        />
        <input
          type="number"
          min={field.min}
          max={field.max}
          step={field.step}
          value={value}
          onChange={(event) => onChange(Number(event.target.value))}
          className="w-20 px-2 py-1 bg-bg-2 border border-border text-text font-mono text-2xs outline-none focus:border-border-strong"
        />
      </div>
    </label>
  )
}

function WhyNotPanel({
  whyNot,
  loading,
}: {
  whyNot?: WhyNotResponse
  loading: boolean
}) {
  if (loading) {
    return <p className="font-mono text-2xs text-text-faint">Building recommendation reasoning...</p>
  }

  if (!whyNot) {
    return <p className="font-mono text-2xs text-text-faint">Recommendation notes unavailable.</p>
  }

  return (
    <div className="space-y-3">
      <div className="border border-green/20 bg-green/5 px-3 py-2">
        <p className="font-mono text-2xs text-green mb-1">Recommended: Variant {whyNot.recommended_variant}</p>
        <p className="font-mono text-2xs text-text-muted leading-relaxed">{whyNot.why_recommended}</p>
      </div>
      {([
        ['A', whyNot.why_not_a],
        ['B', whyNot.why_not_b],
        ['C', whyNot.why_not_c],
      ] as const).map(([label, text]) => (
        <div key={label} className="border border-border bg-bg-2 px-3 py-2">
          <p className="font-mono text-2xs text-text mb-1">Variant {label}</p>
          <p className="font-mono text-2xs text-text-muted leading-relaxed">{text}</p>
        </div>
      ))}
    </div>
  )
}

function DiaryPanel({
  entries,
  loading,
}: {
  entries: DiaryEntry[]
  loading: boolean
}) {
  if (loading) {
    return <p className="font-mono text-2xs text-text-faint">Loading design history...</p>
  }

  if (entries.length === 0) {
    return <p className="font-mono text-2xs text-text-faint">No design diary entries yet.</p>
  }

  return (
    <div className="space-y-2">
      {entries.slice(-6).reverse().map((entry) => (
        <div key={entry.id} className="border border-border bg-bg-2 px-3 py-2">
          <div className="flex items-center justify-between gap-3 mb-1">
            <span className="font-mono text-2xs text-text uppercase">{entry.entry_type.replace(/_/g, ' ')}</span>
            <span className="font-mono text-2xs text-text-faint">{relativeTime(entry.created_at)}</span>
          </div>
          {entry.note && (
            <p className="font-mono text-2xs text-text-muted leading-relaxed">{entry.note}</p>
          )}
        </div>
      ))}
    </div>
  )
}

function SimilarDesignsPanel({
  items,
  loading,
  onOpen,
}: {
  items: SimilarDesignItem[]
  loading: boolean
  onOpen: (designId: string) => void
}) {
  if (loading) {
    return <p className="font-mono text-2xs text-text-faint">Searching your design history...</p>
  }

  if (items.length === 0) {
    return <p className="font-mono text-2xs text-text-faint">No similar designs found yet.</p>
  }

  return (
    <div className="space-y-2">
      {items.map((item) => (
        <button
          key={item.id}
          onClick={() => onOpen(item.id)}
          className="w-full text-left border border-border bg-bg-2 px-3 py-2 hover:border-border-strong transition-colors"
        >
          <div className="flex items-center justify-between gap-3">
            <p className="font-mono text-2xs text-text truncate">
              {item.prompt ?? item.name ?? 'Untitled design'}
            </p>
            <span className="font-mono text-2xs text-blue">{Math.round(item.similarity_score * 100)}%</span>
          </div>
          <p className="font-mono text-2xs text-text-faint mt-1">
            Variant {item.recommended_variant ?? '-'} · {relativeTime(item.created_at)}
          </p>
        </button>
      ))}
    </div>
  )
}

function MaterialRecommendationsPanel({
  items,
  loading,
}: {
  items: MaterialRecommendationItem[]
  loading: boolean
}) {
  if (loading) {
    return <p className="font-mono text-2xs text-text-faint">Scoring candidate materials...</p>
  }

  if (items.length === 0) {
    return <p className="font-mono text-2xs text-text-faint">No material recommendations available.</p>
  }

  return (
    <div className="space-y-2">
      {items.map((item) => (
        <div key={item.slug} className="border border-border bg-bg-2 px-3 py-2">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Lightbulb className="h-3.5 w-3.5 text-blue" />
              <span className="font-mono text-2xs text-text">{item.name}</span>
            </div>
            <span className="font-mono text-2xs text-blue">{item.score.toFixed(1)}</span>
          </div>
          <p className="font-mono text-2xs text-text-muted mt-1 leading-relaxed">{item.tradeoff}</p>
          {item.reasons.length > 0 && (
            <p className="font-mono text-2xs text-text-faint mt-2 leading-relaxed">
              {item.reasons.join(' ')}
            </p>
          )}
        </div>
      ))}
    </div>
  )
}

function normalizeProcess(process: unknown): 'cnc' | 'sheet_metal' | 'print' {
  if (process === 'sheet_metal' || process === 'print') {
    return process
  }
  return 'cnc'
}
