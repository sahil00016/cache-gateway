/** Presentational pieces for the dashboard. */

import type { ReactNode } from 'react'

export function Stat({
  label,
  value,
  unit,
  hint,
  tone = 'normal',
}: {
  label: string
  value: string | number
  unit?: string
  hint?: string
  tone?: 'normal' | 'good' | 'bad'
}) {
  return (
    <div className={`stat stat--${tone}`}>
      <div className="stat__label">{label}</div>
      <div className="stat__value">
        {value}
        {unit && <span className="stat__unit">{unit}</span>}
      </div>
      {hint && <div className="stat__hint">{hint}</div>}
    </div>
  )
}

export function Panel({
  title,
  subtitle,
  children,
}: {
  title: string
  subtitle?: string
  children: ReactNode
}) {
  return (
    <section className="panel">
      <header className="panel__head">
        <h2>{title}</h2>
        {subtitle && <p>{subtitle}</p>}
      </header>
      {children}
    </section>
  )
}

export function Toggle({
  label,
  description,
  checked,
  disabled,
  onChange,
}: {
  label: string
  description: string
  checked: boolean
  disabled?: boolean
  onChange: (next: boolean) => void
}) {
  return (
    <label className={`toggle ${disabled ? 'toggle--busy' : ''}`}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="toggle__text">
        <strong>{label}</strong>
        <em>{description}</em>
      </span>
      <span className={`toggle__state ${checked ? 'on' : 'off'}`}>{checked ? 'ON' : 'OFF'}</span>
    </label>
  )
}

/**
 * Minimal sparkline. Deliberately hand-drawn SVG rather than a charting
 * dependency: one polyline does not justify shipping a library.
 */
export function Sparkline({
  points,
  max,
  height = 44,
}: {
  points: (number | null)[]
  max?: number
  height?: number
}) {
  const valid = points.filter((p): p is number => p !== null)
  if (valid.length < 2) return <div className="spark spark--empty">collecting…</div>

  const ceiling = max ?? Math.max(...valid, 1)
  const width = 240
  const step = width / (points.length - 1)

  // Nulls break the line into segments rather than being drawn as zero, which
  // would invent a dip that never happened.
  const segments: string[] = []
  let current: string[] = []
  points.forEach((p, i) => {
    if (p === null) {
      if (current.length > 1) segments.push(current.join(' '))
      current = []
      return
    }
    const x = i * step
    const y = height - (Math.min(p, ceiling) / ceiling) * height
    current.push(`${x.toFixed(1)},${y.toFixed(1)}`)
  })
  if (current.length > 1) segments.push(current.join(' '))

  return (
    <svg className="spark" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
      {segments.map((pts, i) => (
        <polyline key={i} points={pts} fill="none" strokeWidth="2" />
      ))}
    </svg>
  )
}

export function Meter({ fraction, caption }: { fraction: number; caption: string }) {
  const pct = Math.max(0, Math.min(1, fraction)) * 100
  return (
    <div className="meter">
      <div className="meter__track">
        <div className="meter__fill" style={{ width: `${pct}%` }} />
      </div>
      <span className="meter__caption">{caption}</span>
    </div>
  )
}
