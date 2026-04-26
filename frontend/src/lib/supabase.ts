import { createClient } from '@supabase/supabase-js'
import type {
  AuthChangeEvent,
  Session,
  SupabaseClient,
  User,
} from '@supabase/supabase-js'


type AuthResponse = { error: Error | null }
type SessionResponse = { data: { session: Session | null } }
type Subscription = { unsubscribe: () => void }

interface SupabaseFacade {
  auth: {
    getSession: () => Promise<SessionResponse>
    onAuthStateChange: (
      callback: (event: AuthChangeEvent, session: Session | null) => void,
    ) => { data: { subscription: Subscription } }
    signUp: (credentials: { email: string; password: string }) => Promise<AuthResponse>
    signInWithPassword: (
      credentials: { email: string; password: string },
    ) => Promise<AuthResponse>
    signOut: () => Promise<AuthResponse>
  }
}

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY

export const supabase: SupabaseFacade = supabaseUrl && supabaseAnonKey
  ? createLiveClient(createClient(supabaseUrl, supabaseAnonKey))
  : createMockClient()


function createLiveClient(client: SupabaseClient): SupabaseFacade {
  return {
    auth: {
      getSession: () => client.auth.getSession(),
      onAuthStateChange: (callback) => client.auth.onAuthStateChange(callback),
      signUp: async (credentials) => {
        const { error } = await client.auth.signUp(credentials)
        return { error: error as Error | null }
      },
      signInWithPassword: async (credentials) => {
        const { error } = await client.auth.signInWithPassword(credentials)
        return { error: error as Error | null }
      },
      signOut: async () => {
        const { error } = await client.auth.signOut()
        return { error: error as Error | null }
      },
    },
  }
}

function createMockClient(): SupabaseFacade {
  let session: Session | null = null
  const subscribers = new Set<(event: AuthChangeEvent, session: Session | null) => void>()

  return {
    auth: {
      getSession: async () => ({ data: { session } }),
      onAuthStateChange: (callback) => {
        subscribers.add(callback)
        return {
          data: {
            subscription: {
              unsubscribe: () => subscribers.delete(callback),
            },
          },
        }
      },
      signUp: async () => ({
        error: new Error(
          'Supabase is not configured. Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY in frontend/.env.',
        ),
      }),
      signInWithPassword: async () => ({
        error: new Error(
          'Supabase is not configured. Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY in frontend/.env.',
        ),
      }),
      signOut: async () => {
        session = null
        subscribers.forEach((callback) => callback('SIGNED_OUT', null))
        return { error: null }
      },
    },
  }
}
