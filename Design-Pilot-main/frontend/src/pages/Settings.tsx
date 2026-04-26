import { useAuth } from '@/store'

export function SettingsPage() {
  const { user } = useAuth()

  return (
    <div className="min-h-screen bg-bg pt-12">
      <div className="max-w-3xl mx-auto px-6 py-8">
        <h1 className="font-mono text-lg text-text">Settings</h1>
        <p className="mt-2 font-mono text-xs text-text-muted">
          Account and local development configuration.
        </p>

        <div className="mt-8 border border-border bg-bg-2 p-5 space-y-5">
          <div>
            <p className="label-xs mb-2">ACCOUNT</p>
            <p className="font-mono text-xs text-text">{user?.email ?? 'No signed-in user'}</p>
          </div>

          <div>
            <p className="label-xs mb-2">LOCAL TESTING</p>
            <p className="font-mono text-xs text-text-muted leading-6">
              Set `frontend/.env` with Supabase values and the backend `.env` with database,
              Redis, storage, and Anthropic keys before full end-to-end testing.
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
