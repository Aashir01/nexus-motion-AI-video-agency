import React, { useEffect, useRef, useState } from 'react'
import { api, streamJob } from '../api'
import { Alert, Badge, Loading, Progress, Stat, formatDuration, formatUsd } from '../components/ui'

const STAGE_LABELS = {
  bible: 'Show bible', outline: 'Episode outline', scene_grid: 'Scene grid',
  storyboard: 'Shot breakdown', continuity: 'Continuity audit', casting: 'Voice casting',
  references: 'Reference sheets', prompts: 'Shot prompts', keyframes: 'Keyframes',
  dialogue: 'Dialogue', shots: 'Shot generation', score: 'Score',
  assemble: 'Assembly', critique: 'Editor review', pipeline: 'Production',
}
const ORDER = Object.keys(STAGE_LABELS).filter((s) => s !== 'pipeline')

export default function Production({ id }) {
  const [job, setJob] = useState(null)
  const [plan, setPlan] = useState(null)
  const [events, setEvents] = useState([])
  const [stages, setStages] = useState({})
  const [progress, setProgress] = useState(0)
  const [current, setCurrent] = useState('')
  const [error, setError] = useState('')
  const logRef = useRef(null)

  useEffect(() => {
    api.getJob(id).then(setJob).catch((e) => setError(e.message))
  }, [id])

  useEffect(() => {
    const stop = streamJob(id, (event) => {
      setEvents((prev) => [...prev.slice(-400), event])
      if (typeof event.overall_pct === 'number') setProgress(event.overall_pct)
      if (event.stage && event.stage !== 'pipeline') {
        setCurrent(event.stage)
        setStages((prev) => ({
          ...prev,
          [event.stage]: {
            status: event.status,
            message: event.message,
            completed: event.completed,
            total: event.total,
          },
        }))
      }
    }, () => {
      api.getJob(id).then((j) => {
        setJob(j)
        if (j.episode_id) api.getEpisodePlan(j.episode_id).then(setPlan).catch(() => {})
      }).catch(() => {})
    })
    return stop
  }, [id])

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [events])

  // Keep the job row fresh while it runs, for cost and status.
  useEffect(() => {
    if (!job || ['succeeded', 'failed', 'cancelled'].includes(job.status)) return
    const t = setInterval(() => api.getJob(id).then(setJob).catch(() => {}), 8000)
    return () => clearInterval(t)
  }, [job, id])

  async function cancel() {
    try {
      setJob(await api.cancelJob(id))
    } catch (err) {
      setError(err.message)
    }
  }

  if (!job) return <Loading label="Loading production…" />

  const live = job.status === 'running' || job.status === 'queued'
  const result = job.result
  const shots = plan ? (plan.episode?.scenes || []).flatMap((s) => s.shots || []) : []
  const videoUrl = result?.video_url || plan?.episode?.final_video_url

  return (
    <div className="content">
      <div className="page-head">
        <div>
          <div className="row small dim" style={{ marginBottom: 5 }}>
            <a href="#/">Studio</a> <span>/</span> <span>Production</span>
          </div>
          <h1>{result?.title || plan?.episode?.title || 'Production in progress'}</h1>
          <p className="mono tiny dim">{job.id}</p>
        </div>
        <div className="row">
          <Badge status={job.status} />
          {live && <button className="btn btn-danger btn-sm" onClick={cancel}>Cancel</button>}
        </div>
      </div>

      <Alert kind="error">{error || job.error}</Alert>

      <div className="card">
        <div className="row-between" style={{ marginBottom: 10 }}>
          <strong>{STAGE_LABELS[current] || job.stage || 'Queued'}</strong>
          <span className="muted small">{Math.round(progress || job.progress_pct)}%</span>
        </div>
        <Progress value={progress || job.progress_pct} />
        <div className="small muted" style={{ marginTop: 9 }}>
          {events.length ? events[events.length - 1].message : job.message}
        </div>
      </div>

      <div className="grid grid-4" style={{ margin: '16px 0' }}>
        <Stat label="Spent" value={formatUsd(job.cost_usd)}
              sub={`${job.credits_reserved.toLocaleString()} credits held`} />
        <Stat label="Shots" value={result?.shots ?? shots.length ?? '—'} />
        <Stat label="Runtime"
              value={formatDuration(result?.actual_seconds ?? plan?.episode?.actual_seconds)} />
        <Stat label="Scenes" value={result?.scenes ?? plan?.episode?.scenes?.length ?? '—'} />
      </div>

      {videoUrl && (
        <div className="card">
          <div className="card-head"><h2>The cut</h2>
            <a className="btn btn-sm" href={videoUrl} target="_blank" rel="noreferrer">Download</a>
          </div>
          <video controls src={videoUrl} />
        </div>
      )}

      <div className="grid grid-2" style={{ marginTop: 14, alignItems: 'start' }}>
        <div className="card">
          <div className="card-head"><h2>Pipeline</h2></div>
          {ORDER.map((name) => {
            const stage = stages[name]
            const status = stage?.status
            const icon = { completed: '✓', failed: '✗', skipped: '·', started: '▸',
                           progress: '▸' }[status] || '○'
            const color = { completed: 'var(--success)', failed: 'var(--danger)',
                            started: 'var(--accent)', progress: 'var(--accent)' }[status]
                          || 'var(--text-dim)'
            return (
              <div key={name} className="stage-row">
                <span style={{ color, width: 14 }}>{icon}</span>
                <span className="stage-name">{STAGE_LABELS[name]}</span>
                <span className="small muted" style={{ flex: 1, minWidth: 0,
                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {stage?.message || ''}
                </span>
                {stage?.total > 0 && status === 'progress' && (
                  <span className="tiny dim">{stage.completed}/{stage.total}</span>
                )}
              </div>
            )
          })}
        </div>

        <div className="card">
          <div className="card-head"><h2>Log</h2>
            <span className="tiny dim">{events.length} events</span></div>
          <div className="log" ref={logRef}>
            {events.length === 0 && <div className="log-line dim">waiting for the worker…</div>}
            {events.map((e, i) => (
              <div key={i} className="log-line">
                <span className="ts">{new Date((e.ts || 0) * 1000).toLocaleTimeString()} </span>
                <span style={{ color: e.status === 'failed' ? 'var(--danger)' : undefined }}>
                  [{e.stage}] {e.message}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {shots.length > 0 && (
        <div className="card" style={{ marginTop: 14 }}>
          <div className="card-head">
            <h2>Shot grid</h2>
            <span className="tiny dim">
              keyframes drive the clips — this is where consistency is visible
            </span>
          </div>
          <div className="shot-grid">
            {shots.map((shot) => (
              <div key={shot.index} className="shot-tile">
                {shot.keyframe_url
                  ? <img src={shot.keyframe_url} alt={`Shot ${shot.index}`} loading="lazy" />
                  : <div style={{ aspectRatio: '16/9', background: 'var(--bg-hover)' }} />}
                <div className="shot-meta">
                  <div className="row-between">
                    <strong>#{shot.index}</strong>
                    <span className="dim">{(shot.actual_seconds || shot.target_seconds).toFixed(1)}s</span>
                  </div>
                  <div className="dim" style={{ overflow: 'hidden', textOverflow: 'ellipsis',
                                                whiteSpace: 'nowrap' }}>
                    {shot.shot_size?.replace(/_/g, ' ')}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {result?.cost?.by_model && Object.keys(result.cost.by_model).length > 0 && (
        <div className="card" style={{ marginTop: 14 }}>
          <div className="card-head"><h2>Where the money went</h2>
            <span className="tiny dim">{result.cost.fallbacks} call(s) fell back</span></div>
          <table>
            <thead><tr><th>Model</th><th style={{ textAlign: 'right' }}>Cost</th></tr></thead>
            <tbody>
              {Object.entries(result.cost.by_model).slice(0, 12).map(([model, cost]) => (
                <tr key={model}>
                  <td className="mono small">{model}</td>
                  <td className="small" style={{ textAlign: 'right' }}>{formatUsd(cost)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {result?.warnings?.length > 0 && (
        <div className="card" style={{ marginTop: 14 }}>
          <div className="card-head"><h2>Warnings</h2></div>
          <ul className="small muted" style={{ margin: 0, paddingLeft: 18 }}>
            {result.warnings.slice(0, 12).map((w, i) => <li key={i}>{w}</li>)}
          </ul>
        </div>
      )}
    </div>
  )
}
