import React, { useEffect, useState } from 'react'
import { api } from '../api'
import { Alert, Badge, Empty, Loading, formatDuration, formatUsd } from '../components/ui'

const PROFILE_COPY = {
  offline: 'Offline engine — animatic only, costs nothing, always available.',
  free: 'Free provider tiers. Rate limited and rough, but real output.',
  budget: 'Cheapest paid models. Good for previz and rough cuts.',
  balanced: 'Seedream keyframes into Kling 2.5. The best cost-to-consistency ratio.',
  premium: 'Nano Banana Pro keyframes into Kling 3.0 Pro, ElevenLabs v3 dialogue.',
  flagship: 'Veo 3.1 / Seedance 2.5 shots, Opus 5 writing. No compromises.',
}

export default function SeriesDetail({ id, navigate }) {
  const [series, setSeries] = useState(null)
  const [episodes, setEpisodes] = useState([])
  const [bible, setBible] = useState(null)
  const [account, setAccount] = useState(null)
  const [profiles, setProfiles] = useState([])
  const [quote, setQuote] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [tab, setTab] = useState('episodes')
  const [form, setForm] = useState({
    premise: '', target_minutes: 12, profile: 'balanced', resolution: '1080p',
    generate_score: true, subtitles: true, burn_subtitles: false, critique: true,
  })

  async function load() {
    try {
      const [s, e, b, a, p] = await Promise.all([
        api.getSeries(id), api.listEpisodes(id), api.getBible(id).catch(() => null),
        api.account(), api.profiles(),
      ])
      setSeries(s); setEpisodes(e); setAccount(a)
      setProfiles(p.profiles)
      setBible(b && !b.status ? b : null)
      const allowed = p.profiles.filter((x) => x.available_to_plan).map((x) => x.name)
      if (!allowed.includes(form.profile)) {
        setForm((f) => ({ ...f, profile: a.plan.default_profile }))
      }
      setForm((f) => ({
        ...f,
        target_minutes: Math.min(f.target_minutes, a.plan.max_episode_minutes),
      }))
    } catch (err) {
      setError(err.message)
    }
  }

  useEffect(() => { load() }, [id])

  useEffect(() => {
    let cancelled = false
    api.quote({ target_minutes: Number(form.target_minutes), profile: form.profile })
      .then((q) => !cancelled && setQuote(q))
      .catch(() => !cancelled && setQuote(null))
    return () => { cancelled = true }
  }, [form.target_minutes, form.profile])

  async function start(e) {
    e.preventDefault()
    setBusy(true); setError('')
    try {
      const job = await api.startProduction({
        series_id: id, ...form, target_minutes: Number(form.target_minutes),
      })
      navigate(`/production/${job.id}`)
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  if (!series) return <Loading />

  const allowedProfiles = profiles.filter((p) => p.available_to_plan)
  const maxMinutes = account?.plan.max_episode_minutes ?? 15
  const affordable = quote?.affordable !== false

  return (
    <div className="content">
      <div className="page-head">
        <div>
          <div className="row small dim" style={{ marginBottom: 5 }}>
            <a href="#/">Studio</a> <span>/</span> <span>{series.title}</span>
          </div>
          <h1>{series.title}</h1>
          <p>{series.logline || series.brief}</p>
        </div>
      </div>

      <Alert kind="error">{error}</Alert>

      <div className="row" style={{ gap: 6, marginBottom: 16 }}>
        {['episodes', 'bible', 'produce'].map((t) => (
          <button key={t} className={`btn btn-sm ${tab === t ? 'btn-primary' : ''}`}
                  onClick={() => setTab(t)}>
            {t === 'bible' ? 'Show bible' : t === 'produce' ? 'New production' : 'Episodes'}
          </button>
        ))}
      </div>

      {tab === 'episodes' && (
        episodes.length === 0 ? (
          <Empty icon="🎞" title="No episodes yet">
            Produce episode one — it writes the bible, generates the cast reference
            sheets and locks the look for everything that follows.
          </Empty>
        ) : (
          <div className="stack">
            {episodes.map((ep) => (
              <div key={ep.id} className="card">
                <div className="row-between wrap">
                  <div style={{ minWidth: 0 }}>
                    <div className="row" style={{ gap: 9 }}>
                      <strong>Ep {ep.number}. {ep.title || 'Untitled'}</strong>
                      <Badge status={ep.status} />
                    </div>
                    <div className="small muted" style={{ marginTop: 4 }}>{ep.premise || '—'}</div>
                    <div className="row tiny dim wrap" style={{ marginTop: 7, gap: 12 }}>
                      <span>{formatDuration(ep.duration_seconds)} runtime</span>
                      <span>{ep.shot_count} shots</span>
                      <span>{formatUsd(ep.cost_usd)} spent</span>
                    </div>
                  </div>
                  {ep.video_url && (
                    <a className="btn btn-sm" href={ep.video_url} target="_blank" rel="noreferrer">
                      Download
                    </a>
                  )}
                </div>
                {ep.video_url && (
                  <video controls preload="metadata" poster={ep.thumbnail_url || undefined}
                         src={ep.video_url} style={{ marginTop: 12 }}>
                    {ep.subtitle_url && (
                      <track kind="subtitles" src={ep.subtitle_url} srcLang="en" label="English" default />
                    )}
                  </video>
                )}
              </div>
            ))}
          </div>
        )
      )}

      {tab === 'bible' && (
        !bible ? (
          <Empty icon="📖" title="The bible is written during the first production">
            Once episode one runs, the cast, wardrobe, locations and reference
            portraits are locked here and reused by every later episode.
          </Empty>
        ) : (
          <>
            <div className="card">
              <div className="card-head"><h2>{bible.title}</h2>
                <span className="badge badge-accent">{bible.genre}</span></div>
              <p className="muted">{bible.logline}</p>
              <div className="grid grid-2" style={{ marginTop: 12 }}>
                <div>
                  <div className="stat-label">Tone</div>
                  <div className="small">{bible.tone}</div>
                </div>
                <div>
                  <div className="stat-label">Look</div>
                  <div className="small">
                    {bible.visual_style?.look}, {bible.visual_style?.color_grade}
                  </div>
                </div>
              </div>
            </div>

            <h2 style={{ margin: '22px 0 12px' }}>Cast</h2>
            <div className="grid grid-3">
              {(bible.characters || []).map((c) => {
                const portrait = (c.reference_images || []).find((r) => r.is_canonical)
                  || (c.reference_images || [])[0]
                return (
                  <div key={c.id} className="character-card">
                    {portrait
                      ? <img className="character-portrait" src={portrait.url} alt={c.name} />
                      : <div className="character-portrait" />}
                    <div className="character-body">
                      <div className="row-between">
                        <strong>{c.name}</strong>
                        <span className="badge">{c.role}</span>
                      </div>
                      <div className="small muted" style={{ marginTop: 4 }}>{c.one_line}</div>
                      <div className="tiny dim" style={{ marginTop: 8 }}>
                        {c.appearance?.hair} · {c.appearance?.eyes}
                      </div>
                      <div className="tiny dim">Voice: {c.voice?.voice_name || 'unassigned'}</div>
                      <div className="tiny dim" style={{ marginTop: 4 }}>
                        {(c.reference_images || []).length} reference image(s)
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>

            <h2 style={{ margin: '22px 0 12px' }}>Locations</h2>
            <div className="grid grid-3">
              {(bible.locations || []).map((l) => (
                <div key={l.id} className="card">
                  <div className="row-between">
                    <strong>{l.name}</strong>
                    <span className="badge">{l.interior_exterior}</span>
                  </div>
                  <div className="small muted" style={{ marginTop: 5 }}>{l.description}</div>
                  <div className="tiny dim" style={{ marginTop: 7 }}>{l.lighting_signature}</div>
                </div>
              ))}
            </div>
          </>
        )
      )}

      {tab === 'produce' && (
        <form className="card" onSubmit={start}>
          <h2 style={{ marginBottom: 14 }}>Produce a new episode</h2>

          <div className="field">
            <label>Episode premise</label>
            <textarea value={form.premise} style={{ minHeight: 76 }}
                      placeholder="Maya has one night to return a favour before it stops being one."
                      onChange={(e) => setForm({ ...form, premise: e.target.value })} />
            <div className="hint">
              Leave blank and the screenwriter picks the strongest next episode from
              the series arc.
            </div>
          </div>

          <div className="grid grid-2">
            <div className="field">
              <label>Runtime — {form.target_minutes} minutes</label>
              <input type="range" min="1" max={maxMinutes} step="0.5" value={form.target_minutes}
                     onChange={(e) => setForm({ ...form, target_minutes: e.target.value })} />
              <div className="hint">Your plan allows up to {maxMinutes} minutes.</div>
            </div>
            <div className="field">
              <label>Resolution</label>
              <select value={form.resolution}
                      onChange={(e) => setForm({ ...form, resolution: e.target.value })}>
                {(account?.plan.resolutions || ['720p']).map((r) => (
                  <option key={r} value={r}>{r}</option>
                ))}
              </select>
            </div>
          </div>

          <div className="field">
            <label>Quality profile</label>
            <select value={form.profile}
                    onChange={(e) => setForm({ ...form, profile: e.target.value })}>
              {allowedProfiles.map((p) => (
                <option key={p.name} value={p.name}>{p.label}</option>
              ))}
            </select>
            <div className="hint">{PROFILE_COPY[form.profile]}</div>
          </div>

          <div className="row wrap" style={{ gap: 18, marginBottom: 16 }}>
            {[['generate_score', 'Original score'], ['subtitles', 'Subtitles'],
              ['burn_subtitles', 'Burn subtitles in'], ['critique', 'Editor review pass']].map(
              ([key, label]) => (
                <label key={key} className="check">
                  <input type="checkbox" checked={form[key]}
                         onChange={(e) => setForm({ ...form, [key]: e.target.checked })} />
                  {label}
                </label>
              ))}
          </div>

          {quote && (
            <div className={`alert ${affordable ? 'alert-info' : 'alert-error'}`}>
              <div className="row-between wrap">
                <div>
                  <strong>{quote.estimated_credits.toLocaleString()} credits</strong>
                  <span className="muted small"> · about {quote.estimated_shots} shots
                    · raw provider cost {formatUsd(quote.estimated_cost_usd)}</span>
                </div>
                <div className="small muted">
                  balance {quote.credit_balance.toLocaleString()}
                </div>
              </div>
              {!affordable && (
                <div className="small" style={{ marginTop: 6 }}>
                  Not enough credits. <a href="#/billing">Top up or upgrade →</a>
                </div>
              )}
              {quote.routing_plan?.shot_video && (
                <div className="chain" style={{ marginTop: 8 }}>
                  shots: <span className="head">{quote.routing_plan.shot_video[0]}</span>
                  {quote.routing_plan.shot_video.slice(1, 3).map((m) => ` → ${m}`)}
                </div>
              )}
            </div>
          )}

          <button className="btn btn-primary" disabled={busy || !affordable}>
            {busy ? <span className="spinner" /> : 'Start production'}
          </button>
        </form>
      )}
    </div>
  )
}
