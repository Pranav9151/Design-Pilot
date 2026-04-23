import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import { Cpu, Eye, EyeOff, Loader2, AlertCircle } from 'lucide-react'
import { supabase } from '@/lib/supabase'
import { cn } from '@/lib/utils'

type Mode = 'signin' | 'signup'

export function AuthPage({ initialMode = 'signin' }: { initialMode?: Mode }) {
  const navigate    = useNavigate()
  const [mode, setMode]       = useState<Mode>(initialMode)
  const [email, setEmail]     = useState('')
  const [password, setPass]   = useState('')
  const [showPass, setShowP]  = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setSuccess(null)
    setLoading(true)

    try {
      if (mode === 'signup') {
        const { error: err } = await supabase.auth.signUp({ email, password })
        if (err) throw err
        setSuccess('Check your email to confirm your account, then sign in.')
      } else {
        const { error: err } = await supabase.auth.signInWithPassword({ email, password })
        if (err) throw err
        navigate('/studio')
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Authentication failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-bg flex items-center justify-center px-4">
      {/* Background grid */}
      <div
        className="absolute inset-0 opacity-[0.02]"
        style={{
          backgroundImage: 'linear-gradient(#3B82F6 1px, transparent 1px), linear-gradient(90deg, #3B82F6 1px, transparent 1px)',
          backgroundSize: '40px 40px',
        }}
      />

      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
        className="relative z-10 w-full max-w-sm"
      >
        {/* Logo */}
        <div className="flex items-center gap-2 mb-10">
          <Cpu className="h-5 w-5 text-blue" />
          <span className="font-mono text-base font-medium text-text">
            DesignPilot<span className="text-blue">.</span>MECH
          </span>
        </div>

        {/* Mode tabs */}
        <div className="flex mb-6 border-b border-border">
          {(['signin', 'signup'] as Mode[]).map(m => (
            <button
              key={m}
              onClick={() => { setMode(m); setError(null); setSuccess(null) }}
              className={cn(
                'flex-1 pb-2 font-mono text-xs transition-colors border-b-2 -mb-px',
                mode === m
                  ? 'text-text border-blue'
                  : 'text-text-muted border-transparent hover:text-text',
              )}
            >
              {m === 'signin' ? 'Sign In' : 'Create Account'}
            </button>
          ))}
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-3">
          {/* Email */}
          <div>
            <label className="label-xs block mb-1.5">EMAIL</label>
            <input
              type="email"
              value={email}
              onChange={e => setEmail(e.target.value)}
              required
              className="w-full bg-bg-2 border border-border px-3 py-2.5 font-mono text-sm text-text placeholder:text-text-faint outline-none focus:border-blue transition-colors"
              placeholder="engineer@company.com"
            />
          </div>

          {/* Password */}
          <div>
            <label className="label-xs block mb-1.5">PASSWORD</label>
            <div className="relative">
              <input
                type={showPass ? 'text' : 'password'}
                value={password}
                onChange={e => setPass(e.target.value)}
                required
                minLength={8}
                className="w-full bg-bg-2 border border-border px-3 py-2.5 pr-10 font-mono text-sm text-text placeholder:text-text-faint outline-none focus:border-blue transition-colors"
                placeholder="minimum 8 characters"
              />
              <button
                type="button"
                onClick={() => setShowP(s => !s)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-text-faint hover:text-text transition-colors"
              >
                {showPass ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
              </button>
            </div>
          </div>

          {/* Error */}
          {error && (
            <div className="flex items-start gap-2 border border-red/40 bg-red/5 px-3 py-2">
              <AlertCircle className="h-3.5 w-3.5 flex-shrink-0 mt-0.5 text-red" />
              <span className="font-mono text-2xs text-red">{error}</span>
            </div>
          )}

          {/* Success */}
          {success && (
            <div className="border border-green/40 bg-green/5 px-3 py-2">
              <span className="font-mono text-2xs text-green">{success}</span>
            </div>
          )}

          {/* Submit */}
          <button
            type="submit"
            disabled={loading}
            className="w-full flex items-center justify-center gap-2 py-2.5 bg-blue text-white font-mono text-sm hover:bg-blue-dim transition-colors disabled:opacity-60 disabled:cursor-not-allowed mt-2"
          >
            {loading ? (
              <><Loader2 className="h-3.5 w-3.5 animate-spin" /> {mode === 'signin' ? 'Signing in…' : 'Creating account…'}</>
            ) : (
              mode === 'signin' ? 'Sign In' : 'Create Account'
            )}
          </button>
        </form>

        {/* Dev shortcut notice */}
        {import.meta.env.DEV && (
          <div className="mt-6 border border-amber/30 bg-amber/5 px-3 py-2">
            <p className="font-mono text-2xs text-amber">
              DEV MODE — Supabase auth required. Set VITE_SUPABASE_URL + VITE_SUPABASE_ANON_KEY in frontend/.env
            </p>
          </div>
        )}
      </motion.div>
    </div>
  )
}
