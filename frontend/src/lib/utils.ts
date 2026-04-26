import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'


export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}

export function fmt(value: number | null | undefined, digits = 1, unit = ''): string {
  if (value == null || Number.isNaN(value)) return '-'
  const formatted = new Intl.NumberFormat('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value)
  return unit ? `${formatted} ${unit}` : formatted
}

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return '-'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '-'

  const diffSeconds = Math.round((date.getTime() - Date.now()) / 1000)
  const formatter = new Intl.RelativeTimeFormat('en', { numeric: 'auto' })
  const divisions: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ['year', 60 * 60 * 24 * 365],
    ['month', 60 * 60 * 24 * 30],
    ['week', 60 * 60 * 24 * 7],
    ['day', 60 * 60 * 24],
    ['hour', 60 * 60],
    ['minute', 60],
  ]

  for (const [unit, seconds] of divisions) {
    if (Math.abs(diffSeconds) >= seconds) {
      return formatter.format(Math.round(diffSeconds / seconds), unit)
    }
  }

  return formatter.format(diffSeconds, 'second')
}

export function sfColor(value: number | null | undefined): string {
  if (value == null) return 'text-text-faint'
  if (value >= 3) return 'text-green'
  if (value >= 1.5) return 'text-blue'
  if (value >= 1) return 'text-amber'
  return 'text-red'
}

export function bandColor(value: string | null | undefined): string {
  switch (value) {
    case 'high':
      return 'text-green'
    case 'good':
      return 'text-blue'
    case 'review':
      return 'text-amber'
    case 'do_not_use':
      return 'text-red'
    default:
      return 'text-text-faint'
  }
}
