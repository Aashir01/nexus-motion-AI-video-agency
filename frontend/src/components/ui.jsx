import React from 'react'

export function Badge({ status, children }) {
  const tone = {
    succeeded: 'badge-success', ready: 'badge-success', completed: 'badge-success',
    running: 'badge-info', generating: 'badge-info', queued: 'badge-warning',
    failed: 'badge-danger', cancelled: 'badge-danger', draft: '',
  }[status] || ''
  const live = status === 'running' || status === 'generating'
  return (
    <span className={`badge ${tone}`}>
      {live && <span className="dot pulse" />}
      {children || status}
    </span>
  )
}

export function Progress({ value }) {
  return (
    <div className="progress">
      <div className="progress-fill" style={{ width: `${Math.min(100, Math.max(0, value))}%` }} />
    </div>
  )
}

export function Stat({ label, value, sub }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  )
}

export function Empty({ icon = '◎', title, children }) {
  return (
    <div className="empty">
      <div className="empty-icon">{icon}</div>
      <h3>{title}</h3>
      {children && <p className="muted small" style={{ marginTop: 6 }}>{children}</p>}
    </div>
  )
}

export function Alert({ kind = 'info', children }) {
  if (!children) return null
  return <div className={`alert alert-${kind}`}>{children}</div>
}

export function Spinner({ label }) {
  return (
    <span className="row small muted">
      <span className="spinner" />
      {label}
    </span>
  )
}

export function Loading({ label = 'Loading…' }) {
  return <div className="empty"><Spinner label={label} /></div>
}

export function formatDuration(seconds) {
  if (!seconds && seconds !== 0) return '—'
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

export function formatUsd(value) {
  if (value == null) return '—'
  return value >= 0.01 ? `$${value.toFixed(2)}` : `$${value.toFixed(4)}`
}

export function formatDate(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}
