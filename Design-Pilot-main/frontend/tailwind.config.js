/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg:    { DEFAULT: '#08090A', 1: '#0F1011', 2: '#161719', 3: '#1E2022', 4: '#26292C' },
        border:{ DEFAULT: '#2A2D31', strong: '#3D4147' },
        text:  { DEFAULT: '#E8EAED', muted: '#8B9099', faint: '#4D5260' },
        blue:  { DEFAULT: '#3B82F6', dim: '#1D4ED8', glow: 'rgba(59,130,246,0.15)' },
        amber: { DEFAULT: '#F59E0B', dim: '#B45309', glow: 'rgba(245,158,11,0.12)' },
        green: { DEFAULT: '#22C55E', dim: '#15803D', glow: 'rgba(34,197,94,0.12)' },
        red:   { DEFAULT: '#EF4444', dim: '#B91C1C', glow: 'rgba(239,68,68,0.12)' },
      },
      fontFamily: {
        sans: ['"IBM Plex Sans"', 'system-ui', 'sans-serif'],
        mono: ['"IBM Plex Mono"', 'monospace'],
      },
      animation: {
        'fade-in': 'fadeIn 150ms ease-out',
        'slide-up': 'slideUp 200ms cubic-bezier(0.16,1,0.3,1)',
      },
      keyframes: {
        fadeIn:  { from: { opacity: '0' }, to: { opacity: '1' } },
        slideUp: { from: { opacity: '0', transform: 'translateY(8px)' }, to: { opacity: '1', transform: 'translateY(0)' } },
      },
      boxShadow: {
        'glow-blue': '0 0 20px rgba(59,130,246,0.15)',
        'panel': '0 1px 0 0 rgba(255,255,255,0.04) inset, 0 -1px 0 0 rgba(0,0,0,0.4)',
      },
    },
  },
  plugins: [],
}
