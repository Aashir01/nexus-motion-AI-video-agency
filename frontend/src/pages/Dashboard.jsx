import React, { useEffect, useState } from 'react'
import { api } from '../api'
import { Alert, Badge, Empty, Loading, Stat, formatDate, formatDuration, formatUsd } from '../components/ui'

export default function Dashboard({ navigate }) {
  const [series, setSeries] = useState(null)
  const [jobs, setJobs] = useState([])
  const [account, setAccount] = useState(null)
  const [error, setError] = useState('')
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState({ title: '', brief: '', genre: 'drama', aspect_ratio: '16:9' })

  async function load() {
    try {
      const [s, j, a] = await Promise.all([api.listSeries(), api.listJobs(), api.account()])
      setSeries(s); setJobs(j); setAccount(a)
    } catch (err) {
      setError(err.message)
    }
  }

  useEffect(() => { load() }, [])

  // Poll while anything is in flight so the dashboard reflects reality.
  useEffect(() => {
    const active = jobs.some((j) => j.status === 'running' || j.status === 'queued')
    if (!active) return
    const id = setInterval(() => api.listJobs().then(setJobs).catch(() => {}), 5000)
    return () => clearInterval(id)
  }, [jobs])

  async function create(e) {
    e.preventDefault()
    setError('')
    try {
      const created = await api.createSeries(form)
      navigate(`/series/${created.id}`)
    } catch (err) {
      setError(err.message)
    }
  }

  if (!series) return <Loading />

  const running = jobs.filter((j) => j.status === 'running' || j.status === 'queued')

  return (
    <div className="content">
      <div className="page-head">
        <div>
          <h1>Your studio</h1>
          <p>Each series keeps its own show bible — cast, wardrobe and reference
             portraits — so every new episode stays on model.</p>
        </div>
        <button className="btn btn-primary" onClick={() => setCreating(!creating)}>
          {creating ? 'Cancel' : '+ New series'}
        </button>
      </div>

      <Alert kind="error">{error}</Alert>

      {account && (
        <div className="grid grid-4" style={{ marginBottom: 20 }}>
          <Stat label="Plan" value={account.plan.name}
                sub={`up to ${account.plan.max_episode_minutes} min episodes`} />
          <Stat label="Credits" value={account.credit_balance.toLocaleString()}
                sub={`≈ $${account.credit_balance_usd.toFixed(2)} of production`} />
          <Stat label="Series" value={series.length} />
          <Stat label="In production" value={running.length}
                sub={`${account.plan.max_concurrent_jobs} concurrent max`} />
        </div>
      )}

      {creating && (
        <div className="card" style={{ marginBottom: 18 }}>
          <h2 style={{ marginBottom: 14 }}>New series</h2>
          <form onSubmit={create}>
            <div className="grid grid-2">
              <div className="field">
                <label>Title</label>
                <input required value={form.title} placeholder="Slack Water"
                       onChange={(e) => setForm({ ...form, title: e.target.value })} />
              </div>
              <div className="field">
                <label>Genre</label>
                <input value={form.genre} placeholder="crime drama"
                       onChange={(e) => setForm({ ...form, genre: e.target.value })} />
              </div>
            </div>
            <div className="field">
              <label>Creative brief</label>
              <textarea required minLength={20} value={form.brief}
                        placeholder="A harbour-town drama about a fixer who has run out of favours, and the man who is owed one. Grounded, restrained, mostly at night."
                        onChange={(e) => setForm({ ...form, brief: e.target.value })} />
              <div className="hint">
                The showrunner agent works from this. Say who it is about, what it feels
                like, and what kind of trouble it lives in — the cast, look and locations
                are derived from here and then locked for the whole series.
              </div>
            </div>
            <div className="field" style={{ maxWidth: 220 }}>
              <label>Aspect ratio</label>
              <select value={form.aspect_ratio}
                      onChange={(e) => setForm({ ...form, aspect_ratio: e.target.value })}>
                <option value="16:9">16:9 — widescreen</option>
                <option value="9:16">9:16 — vertical / microdrama</option>
                <option value="2.39:1">2.39:1 — anamorphic</option>
                <option value="1:1">1:1 — square</option>
                <option value="4:5">4:5 — social</option>
              </select>
            </div>
            <button className="btn btn-primary">Create series</button>
          </form>
        </div>
      )}

      {series.length === 0 && !creating ? (
        <Empty icon="🎬" title="No series yet">
          Create one to write the show bible, cast the voices and produce episode one.
        </Empty>
      ) : (
        <div className="stack">
          {series.map((s) => (
            <a key={s.id} className="list-item" href={`#/series/${s.id}`}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="row" style={{ gap: 8 }}>
                  <strong>{s.title}</strong>
                  {s.has_bible
                    ? <span className="badge badge-success">bible locked</span>
                    : <span className="badge">no bible yet</span>}
                </div>
                <div className="small muted" style={{ marginTop: 3 }}>
                  {s.logline || s.brief.slice(0, 120) + (s.brief.length > 120 ? '…' : '')}
                </div>
                {s.characters.length > 0 && (
                  <div className="row tiny dim" style={{ marginTop: 6, gap: 6 }}>
                    {s.characters.slice(0, 4).map((c) => (
                      <span key={c.id} className="badge">{c.name}</span>
                    ))}
                  </div>
                )}
              </div>
              <div className="center" style={{ flex: '0 0 84px' }}>
                <div className="stat-value" style={{ fontSize: 18 }}>{s.episode_count}</div>
                <div className="tiny dim">episodes</div>
              </div>
            </a>
          ))}
        </div>
      )}

      {jobs.length > 0 && (
        <div className="card" style={{ marginTop: 22 }}>
          <div className="card-head"><h2>Recent productions</h2></div>
          <table>
            <thead>
              <tr><th>Started</th><th>Stage</th><th>Progress</th><th>Cost</th>
                  <th>Status</th><th /></tr>
            </thead>
            <tbody>
              {jobs.slice(0, 8).map((j) => (
                <tr key={j.id}>
                  <td className="small muted">{formatDate(j.created_at)}</td>
                  <td className="small">{j.stage || '—'}</td>
                  <td style={{ width: 130 }}>
                    <div className="row" style={{ gap: 8 }}>
                      <div style={{ flex: 1 }}>
                        <div className="progress">
                          <div className="progress-fill" style={{ width: `${j.progress_pct}%` }} />
                        </div>
                      </div>
                      <span className="tiny dim">{Math.round(j.progress_pct)}%</span>
                    </div>
                  </td>
                  <td className="small">{formatUsd(j.cost_usd)}</td>
                  <td><Badge status={j.status} /></td>
                  <td style={{ width: 70 }}>
                    <a className="btn btn-sm" href={`#/production/${j.id}`}>Open</a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
