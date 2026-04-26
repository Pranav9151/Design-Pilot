const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')


export interface ProgressEvent {
  stage: string
  message: string
  pct: number
}

export interface SandboxSummary {
  ok: boolean
  stage: string
  error: string | null
  elapsed_s: number | null
  metrics: Record<string, number>
}

export interface TripleLockSummary {
  score: number
  band: 'high' | 'good' | 'review' | 'do_not_use'
  explanation: string
  lock1: string
  lock2: string
  lock3: string
}

export interface VariantSpec {
  label: string
  name: string
  rationale: string
  base_width_mm: number
  base_depth_mm: number
  base_thickness_mm: number
  wall_height_mm: number
  wall_thickness_mm: number
  fillet_radius_mm: number
  hole_diameter_mm: number
  hole_count_x: number
  hole_count_y: number
  hole_spacing_x_mm: number
  hole_spacing_y_mm: number
}

export interface VariantResult {
  spec: VariantSpec
  analytical_stress_mpa: number | null
  mass_kg: number | null
  cost_usd: number | null
  safety_factor: number | null
  dfm_issues: string[]
  sandbox: SandboxSummary
  triple_lock: TripleLockSummary | null
  step_url: string | null
  glb_url: string | null
  cadquery_code: string
}

export interface DesignParameters {
  recommended?: string
  request?: {
    material_slug?: string
    process?: string
    safety_factor_target?: number
    load?: {
      magnitude_n?: number
      direction?: string
      lever_arm_mm?: number
      type?: string
    }
  }
  qa?: {
    why_recommended?: string
    why_not_a?: string
    why_not_b?: string
    why_not_c?: string
    senior_engineer_questions?: string[]
  } | null
  variants: VariantResult[]
  [key: string]: unknown
}

export interface DesignSummary {
  id: string
  name: string | null
  status: string
  prompt: string | null
  confidence_score: number | null
  confidence_band: string | null
  recommended_variant: string | null
  step_url: string | null
  glb_url: string | null
  created_at: string
}

export interface DesignDetail {
  id: string
  name: string | null
  status: string
  prompt: string | null
  part_type: string
  cadquery_code: string | null
  parameters: DesignParameters
  step_url: string | null
  glb_url: string | null
  confidence_score: number | null
  confidence_explanation: string | null
  lock1_results: Record<string, unknown> | null
  lock2_results: Record<string, unknown> | null
  lock3_results: Record<string, unknown> | null
  simulation: {
    max_stress_mpa?: number
    safety_factor?: number
    method?: string
  } | null
  dfm: { issues?: string[] } | null
  cost: Record<string, unknown> | null
  assumptions: string[]
  material_id: string | null
  created_at: string
}

export interface DiaryEntry {
  id: string
  entry_type: string
  note: string | null
  snapshot: Record<string, unknown>
  created_at: string
}

export interface WhyNotResponse {
  recommended_variant: string
  why_recommended: string
  why_not_a: string
  why_not_b: string
  why_not_c: string
}

export interface SimilarDesignItem {
  id: string
  name: string | null
  prompt: string | null
  similarity_score: number
  recommended_variant: string | null
  created_at: string
}

export interface MaterialRecommendationRequest {
  load_n: number
  process: 'cnc' | 'sheet_metal' | 'print'
  environment: 'indoor' | 'outdoor' | 'marine' | 'high_temp'
  prioritize: 'balanced' | 'strength' | 'weight' | 'cost'
  top_k: number
}

export interface MaterialRecommendationItem {
  slug: string
  name: string
  score: number
  tradeoff: string
  reasons: string[]
}

export interface ParameterPatchRequest {
  base_width_mm?: number
  base_depth_mm?: number
  base_thickness_mm?: number
  wall_height_mm?: number
  wall_thickness_mm?: number
  fillet_radius_mm?: number
  hole_diameter_mm?: number
  hole_spacing_x_mm?: number
  hole_spacing_y_mm?: number
  recommended_variant?: string
}

interface ExplainResponse {
  summary: string
}

interface SimilarDesignsResponse {
  items: SimilarDesignItem[]
}

interface QuestionsResponse {
  questions: string[]
}

interface MaterialRecommendationResponse {
  items: MaterialRecommendationItem[]
}

interface OptimizeResponse {
  goal: string
  recommended_variant: string
  design: DesignDetail
}

interface StreamHandlers {
  onProgress: (event: ProgressEvent) => void
  onComplete: (design: DesignDetail) => void
  onError: (code: string, message: string) => void
}

export async function fetchDesigns(token: string): Promise<DesignSummary[]> {
  const rows = await requestJson<DesignSummary[]>('/api/v1/designs', {
    token,
  })
  return rows.map(normalizeDesignSummary)
}

export async function fetchDesign(token: string, designId: string): Promise<DesignDetail> {
  const design = await requestJson<DesignDetail>(`/api/v1/designs/${designId}`, {
    token,
  })
  return normalizeDesignDetail(design)
}

export async function deleteDesign(token: string, designId: string): Promise<void> {
  await request('/api/v1/designs/' + designId, {
    method: 'DELETE',
    token,
  })
}

export async function fetchDesignDiary(token: string, designId: string): Promise<DiaryEntry[]> {
  return requestJson<DiaryEntry[]>(`/api/v1/designs/${designId}/diary`, { token })
}

export async function fetchWhyNot(token: string, designId: string): Promise<WhyNotResponse> {
  return requestJson<WhyNotResponse>(`/api/v1/designs/${designId}/why-not`, { token })
}

export async function fetchSimilarDesigns(
  token: string,
  designId: string,
  limit = 5,
): Promise<SimilarDesignsResponse> {
  return requestJson<SimilarDesignsResponse>(
    `/api/v1/designs/${designId}/similar?limit=${limit}`,
    { token },
  )
}

export async function fetchDesignQuestions(
  token: string,
  designId: string,
): Promise<QuestionsResponse> {
  return requestJson<QuestionsResponse>(`/api/v1/designs/${designId}/questions`, { token })
}

export async function explainDesign(token: string, designId: string): Promise<ExplainResponse> {
  return requestJson<ExplainResponse>(`/api/v1/designs/${designId}/explain`, {
    method: 'POST',
    token,
  })
}

export async function patchDesignParameters(
  token: string,
  designId: string,
  payload: ParameterPatchRequest,
): Promise<DesignDetail> {
  const design = await requestJson<DesignDetail>(`/api/v1/designs/${designId}/parameters`, {
    method: 'PATCH',
    token,
    body: payload,
  })
  return normalizeDesignDetail(design)
}

export async function optimizeDesign(
  token: string,
  designId: string,
  payload: { goal: string },
): Promise<OptimizeResponse> {
  const result = await requestJson<OptimizeResponse>(`/api/v1/designs/${designId}/optimize`, {
    method: 'POST',
    token,
    body: payload,
  })
  return {
    ...result,
    design: normalizeDesignDetail(result.design),
  }
}

export async function recommendMaterials(
  token: string,
  payload: MaterialRecommendationRequest,
): Promise<MaterialRecommendationResponse> {
  return requestJson<MaterialRecommendationResponse>('/api/v1/materials/recommend', {
    method: 'POST',
    token,
    body: payload,
  })
}

export function streamDesign(
  token: string,
  prompt: string,
  handlers: StreamHandlers,
): AbortController {
  const controller = new AbortController()

  void (async () => {
    try {
      const response = await request('/api/v1/designs/stream', {
        method: 'POST',
        token,
        body: { prompt },
        signal: controller.signal,
      })

      const reader = response.body?.getReader()
      if (!reader) {
        handlers.onError('stream_unavailable', 'Streaming is not supported in this browser.')
        return
      }

      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        buffer = parseSseBuffer(buffer, handlers)
      }

      buffer += decoder.decode()
      parseSseBuffer(buffer, handlers, true)
    } catch (error) {
      if (isAbortError(error)) return
      handlers.onError('network_error', errorMessage(error))
    }
  })()

  return controller
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE'
  token?: string
  body?: unknown
  signal?: AbortSignal
}

async function requestJson<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await request(path, options)
  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}

async function request(path: string, options: RequestOptions = {}): Promise<Response> {
  const response = await fetch(toUrl(path), {
    method: options.method ?? 'GET',
    headers: {
      Accept: 'application/json',
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...(options.token ? { Authorization: `Bearer ${options.token}` } : {}),
    },
    body: options.body ? JSON.stringify(options.body) : undefined,
    signal: options.signal,
  })

  if (response.ok) {
    return response
  }

  const apiError = await readApiError(response)
  throw new Error(apiError)
}

function parseSseBuffer(
  buffer: string,
  handlers: StreamHandlers,
  flush = false,
): string {
  const chunks = buffer.split(/\r?\n\r?\n/)
  const pending = flush ? '' : (chunks.pop() ?? '')

  for (const chunk of chunks) {
    const event = parseSseEvent(chunk)
    if (!event) continue

    if (event.name === 'progress') {
      handlers.onProgress(event.data as ProgressEvent)
      continue
    }
    if (event.name === 'complete') {
      handlers.onComplete(normalizeDesignDetail(event.data as DesignDetail))
      continue
    }
    if (event.name === 'error') {
      const payload = event.data as { code?: string; message?: string }
      handlers.onError(payload.code ?? 'stream_error', payload.message ?? 'Design generation failed.')
    }
  }

  return pending
}

function parseSseEvent(chunk: string): { name: string; data: unknown } | null {
  let name = 'message'
  const dataLines: string[] = []

  for (const rawLine of chunk.split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line) continue
    if (line.startsWith('event:')) {
      name = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trim())
    }
  }

  if (dataLines.length === 0) return null

  try {
    return {
      name,
      data: JSON.parse(dataLines.join('\n')),
    }
  } catch {
    return null
  }
}

async function readApiError(response: Response): Promise<string> {
  try {
    const payload = await response.json()
    return detailMessage(payload?.detail) ?? payload?.message ?? response.statusText
  } catch {
    return response.statusText || 'Request failed'
  }
}

function detailMessage(detail: unknown): string | null {
  if (!detail) return null
  if (typeof detail === 'string') return detail
  if (typeof detail === 'object') {
    const record = detail as Record<string, unknown>
    if (typeof record.message === 'string') return record.message
    if (typeof record.reason === 'string') return record.reason
    if (typeof record.error === 'string') return record.error
  }
  return null
}

function normalizeDesignSummary(design: DesignSummary): DesignSummary {
  return {
    ...design,
    confidence_score: design.confidence_score ?? null,
    confidence_band: design.confidence_band ?? null,
    recommended_variant: design.recommended_variant ?? null,
    step_url: design.step_url ?? null,
    glb_url: design.glb_url ?? null,
  }
}

function normalizeDesignDetail(design: DesignDetail): DesignDetail {
  return {
    ...design,
    parameters: {
      ...(design.parameters ?? {}),
      variants: Array.isArray(design.parameters?.variants) ? design.parameters.variants : [],
    },
    assumptions: Array.isArray(design.assumptions) ? design.assumptions : [],
    step_url: design.step_url ?? null,
    glb_url: design.glb_url ?? null,
  }
}

function toUrl(path: string): string {
  return `${API_BASE_URL}${path}`
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Something went wrong.'
}
