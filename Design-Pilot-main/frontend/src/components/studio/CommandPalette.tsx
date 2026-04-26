import { useEffect, type ReactNode } from 'react'
import { Command } from 'cmdk'
import { motion, AnimatePresence } from 'framer-motion'
import { useNavigate } from 'react-router-dom'
import {
  LayoutDashboard, Wrench, LogOut, Plus, BookOpen,
} from 'lucide-react'
import { supabase } from '@/lib/supabase'

interface Props {
  open: boolean
  onClose: () => void
}

export function CommandPalette({ open, onClose }: Props) {
  const navigate = useNavigate()

  // Close on Escape is handled by cmdk; also close on backdrop click
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  function run(fn: () => void) {
    fn()
    onClose()
  }

  return (
    <AnimatePresence>
      {open && (
        <>
          {/* Backdrop */}
          <motion.div
            key="backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.1 }}
            className="fixed inset-0 z-40 bg-bg/60 backdrop-blur-sm"
            onClick={onClose}
          />

          {/* Palette */}
          <motion.div
            key="palette"
            initial={{ opacity: 0, scale: 0.97, y: -8 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.97, y: -8 }}
            transition={{ duration: 0.12, ease: [0.16, 1, 0.3, 1] }}
            className="fixed left-1/2 top-32 z-50 w-full max-w-lg -translate-x-1/2"
          >
            <Command
              className="overflow-hidden"
              onKeyDown={(e) => { if (e.key === 'Escape') onClose() }}
            >
              <Command.Input placeholder="Type a command…" autoFocus />

              <Command.List className="max-h-72 overflow-y-auto py-2">
                <Command.Empty className="py-8 text-center font-mono text-xs text-text-faint">
                  No commands match.
                </Command.Empty>

                <Command.Group heading="Navigate">
                  <CmdItem
                    icon={<LayoutDashboard className="h-3.5 w-3.5" />}
                    label="Go to Dashboard"
                    onSelect={() => run(() => navigate('/dashboard'))}
                  />
                  <CmdItem
                    icon={<Wrench className="h-3.5 w-3.5" />}
                    label="Open Studio"
                    onSelect={() => run(() => navigate('/studio'))}
                  />
                </Command.Group>

                <Command.Group heading="Design">
                  <CmdItem
                    icon={<Plus className="h-3.5 w-3.5" />}
                    label="New design"
                    onSelect={() => run(() => navigate('/studio'))}
                  />
                </Command.Group>

                <Command.Group heading="Account">
                  <CmdItem
                    icon={<LogOut className="h-3.5 w-3.5" />}
                    label="Sign out"
                    onSelect={() =>
                      run(async () => {
                        await supabase.auth.signOut()
                        navigate('/login')
                      })
                    }
                  />
                </Command.Group>

                <Command.Group heading="Resources">
                  <CmdItem
                    icon={<BookOpen className="h-3.5 w-3.5" />}
                    label="API docs (localhost:8000/docs)"
                    onSelect={() => run(() => window.open('http://localhost:8000/docs', '_blank'))}
                  />
                </Command.Group>
              </Command.List>

              {/* Footer */}
              <div className="border-t border-border px-4 py-2 flex items-center justify-between">
                <span className="font-mono text-2xs text-text-faint">↑↓ navigate · ⏎ select · Esc close</span>
              </div>
            </Command>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  )
}

function CmdItem({
  icon,
  label,
  onSelect,
}: {
  icon: ReactNode
  label: string
  onSelect: () => void
}) {
  return (
    <Command.Item onSelect={onSelect}>
      {icon}
      {label}
    </Command.Item>
  )
}
