import { Suspense, lazy, useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { supabase } from '@/lib/supabase'
import { useAuth } from '@/store'
import { TopNav }       from '@/components/layout/TopNav'

const AuthPage = lazy(() => import('@/pages/Auth').then((m) => ({ default: m.AuthPage })))
const DashboardPage = lazy(() => import('@/pages/Dashboard').then((m) => ({ default: m.DashboardPage })))
const LandingPage = lazy(() => import('@/pages/Landing').then((m) => ({ default: m.LandingPage })))
const SettingsPage = lazy(() => import('@/pages/Settings').then((m) => ({ default: m.SettingsPage })))
const StudioPage = lazy(() => import('@/pages/Studio').then((m) => ({ default: m.StudioPage })))

function ProtectedLayout({ children }: { children: React.ReactNode }) {
  const { user } = useAuth()
  if (!user) return <Navigate to="/auth" replace />
  return (
    <>
      <TopNav />
      {children}
    </>
  )
}

export default function App() {
  const { setSession } = useAuth()

  // Sync Supabase session into Zustand store
  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => setSession(data.session))
    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, session) => {
      setSession(session)
    })
    return () => subscription.unsubscribe()
  }, [setSession])

  return (
    <BrowserRouter>
      <Suspense fallback={<RouteLoading />}>
        <Routes>
          <Route path="/" element={<LandingPage />} />
          <Route path="/auth" element={<Navigate to="/login" replace />} />
          <Route path="/login" element={<AuthPage initialMode="signin" />} />
          <Route path="/signup" element={<AuthPage initialMode="signup" />} />
          <Route
            path="/dashboard"
            element={
              <ProtectedLayout>
                <DashboardPage />
              </ProtectedLayout>
            }
          />
          <Route
            path="/my-designs"
            element={
              <ProtectedLayout>
                <DashboardPage />
              </ProtectedLayout>
            }
          />
          <Route
            path="/studio"
            element={
              <ProtectedLayout>
                <StudioPage />
              </ProtectedLayout>
            }
          />
          <Route
            path="/studio/:id"
            element={
              <ProtectedLayout>
                <StudioPage />
              </ProtectedLayout>
            }
          />
          <Route
            path="/settings"
            element={
              <ProtectedLayout>
                <SettingsPage />
              </ProtectedLayout>
            }
          />
          <Route path="*" element={<Navigate to="/studio" replace />} />
        </Routes>
      </Suspense>
    </BrowserRouter>
  )
}

function RouteLoading() {
  return (
    <div className="min-h-screen bg-bg flex items-center justify-center">
      <p className="font-mono text-xs text-text-muted">Loading…</p>
    </div>
  )
}
