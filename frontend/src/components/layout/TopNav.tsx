import { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { Cpu, LayoutDashboard, Wrench, LogOut, Command } from 'lucide-react'
import { cn } from '@/lib/utils'
import { supabase } from '@/lib/supabase'
import { useAuth } from '@/store'
import { CommandPalette } from '@/components/studio/CommandPalette'

const NAV_ITEMS = [
  { href: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { href: '/studio',    label: 'Studio',    icon: Wrench },
]

export function TopNav() {
  const { pathname } = useLocation()
  const { user }     = useAuth()
  const [paletteOpen, setPaletteOpen] = useState(false)

  // Global Cmd+K listener
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        setPaletteOpen(true)
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [])

  return (
    <>
      <header className="fixed top-0 left-0 right-0 z-30 h-12 bg-bg-1/90 backdrop-blur border-b border-border flex items-center px-4 gap-6">
        {/* Logo */}
        <Link to="/studio" className="flex items-center gap-2 flex-shrink-0">
          <Cpu className="h-4 w-4 text-blue" />
          <span className="font-mono text-sm font-medium text-text">
            DesignPilot<span className="text-blue">.</span>MECH
          </span>
        </Link>

        {/* Nav links */}
        <nav className="flex items-center gap-1">
          {NAV_ITEMS.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              to={href}
              className={cn(
                'flex items-center gap-1.5 px-3 py-1.5 font-mono text-xs transition-colors duration-100',
                pathname.startsWith(href)
                  ? 'text-text bg-bg-3'
                  : 'text-text-muted hover:text-text hover:bg-bg-2',
              )}
            >
              <Icon className="h-3 w-3" />
              {label}
            </Link>
          ))}
        </nav>

        {/* Right side */}
        <div className="ml-auto flex items-center gap-2">
          {/* Cmd+K hint */}
          <button
            onClick={() => setPaletteOpen(true)}
            className="hidden sm:flex items-center gap-1.5 px-2.5 py-1 border border-border text-text-faint hover:text-text hover:border-border-strong transition-colors font-mono text-2xs"
          >
            <Command className="h-2.5 w-2.5" />K
          </button>

          {/* User email */}
          {user?.email && (
            <span className="font-mono text-2xs text-text-faint hidden md:block">
              {user.email}
            </span>
          )}

          {/* Sign out */}
          <button
            onClick={() => supabase.auth.signOut()}
            className="p-1.5 text-text-faint hover:text-text transition-colors"
            title="Sign out"
          >
            <LogOut className="h-3.5 w-3.5" />
          </button>
        </div>
      </header>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </>
  )
}
