/**
 * Global Zustand stores.
 *
 * auth   — Supabase session / user
 * studio — Core Studio ephemeral state (prompt, active design, progress)
 */
import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { Session, User } from '@supabase/supabase-js'
import type { DesignDetail, ProgressEvent } from '@/lib/api'

// ──────────────────────────────────────────────────────────────────────
// Auth store
// ──────────────────────────────────────────────────────────────────────

interface AuthStore {
  session: Session | null
  user: User | null
  setSession: (session: Session | null) => void
  token: () => string | null
}

export const useAuth = create<AuthStore>()(
  persist(
    (set, get) => ({
      session: null,
      user: null,
      setSession: (session) => set({ session, user: session?.user ?? null }),
      token: () => get().session?.access_token ?? null,
    }),
    { name: 'dp-auth', partialize: (s) => ({ session: s.session, user: s.user }) },
  ),
)

// ──────────────────────────────────────────────────────────────────────
// Studio store
// ──────────────────────────────────────────────────────────────────────

type GenerationStatus = 'idle' | 'streaming' | 'done' | 'error'

interface StudioStore {
  // Input
  prompt: string
  setPrompt: (p: string) => void

  // Generation state
  status: GenerationStatus
  progressPct: number
  progressStage: string
  progressMessage: string
  streamError: string | null

  // Result
  activeDesign: DesignDetail | null
  selectedVariant: 'A' | 'B' | 'C'

  // Actions
  startGeneration: () => void
  setProgress: (e: ProgressEvent) => void
  setComplete: (design: DesignDetail) => void
  setError: (msg: string) => void
  resetGeneration: () => void
  setSelectedVariant: (v: 'A' | 'B' | 'C') => void
}

export const useStudio = create<StudioStore>()((set) => ({
  prompt: '',
  setPrompt: (prompt) => set({ prompt }),

  status: 'idle',
  progressPct: 0,
  progressStage: '',
  progressMessage: '',
  streamError: null,

  activeDesign: null,
  selectedVariant: 'C',

  startGeneration: () =>
    set({ status: 'streaming', progressPct: 0, progressStage: '', progressMessage: '', streamError: null, activeDesign: null }),

  setProgress: (e) =>
    set({ progressPct: e.pct, progressStage: e.stage, progressMessage: e.message }),

  setComplete: (design) =>
    set({
      status: 'done',
      activeDesign: design,
      progressPct: 100,
      selectedVariant: (design.parameters?.recommended ?? 'C') as 'A' | 'B' | 'C',
    }),

  setError: (msg) => set({ status: 'error', streamError: msg }),

  resetGeneration: () =>
    set({ status: 'idle', progressPct: 0, progressStage: '', progressMessage: '', streamError: null }),

  setSelectedVariant: (v) => set({ selectedVariant: v }),
}))
