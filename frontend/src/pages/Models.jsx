import React, { useEffect, useState } from 'react'
import { api } from '../api'
import { Loading } from '../components/ui'

const MODALITIES = ['all', 'text', 'image', 'video', 'tts', 'music']
const TIER_TONE = {
  free: 'badge-success', budget: 'badge-info', standard: '',
  premium: 'badge-warning', flagship: 'badge-accent',
}

export default function Models() {
  const [data, setData] = useState(null)
  const [profiles, setProfiles] = useState(null)
  const [modality, setModality] = useState('video')
  const [onlyReady, setOnlyReady] = useState(false)
  const [view, setView] = useState('catalog')

  useEffect(() => {
    const query = modality === 'all' ? '' : `?modality=${modality}`
    api.models(query).then(setData).catch(() => setData({ models: [], count: 0, providers: {} }))
  }, [modality])

  useEffect(() => { api.profiles().then(setProfiles).catch(() => {}) }, [])

  const models = (data?.models || []).filter((m) => !onlyReady || m.available)

  return (
    <div className="content">
      <div className="page-head">
        <div>
          <h1>Models</h1>
          <p>Every model the router can reach, from flagship down to free. A job
             starts at the top of its profile's chain and walks down on failure,
             rate limit or budget — the offline engine is always the last rung, so
             a production never dies outright.</p>
        </div>
      </div>

      <div className="row" style={{ gap: 6, marginBottom: 14 }}>
        <button className={`btn btn-sm ${view === 'catalog' ? 'btn-primary' : ''}`}
                onClick={() => setView('catalog')}>Catalog</button>
        <button className={`btn btn-sm ${view === 'routing' ? 'btn-primary' : ''}`}
                onClick={() => setView('routing')}>Routing</button>
      </div>

      {view === 'catalog' && (
        <>
          <div className="row wrap" style={{ gap: 6, marginBottom: 14 }}>
            {MODALITIES.map((m) => (
              <button key={m} className={`btn btn-sm ${modality === m ? 'btn-primary' : ''}`}
                      onClick={() => setModality(m)}>{m}</button>
            ))}
            <div className="spacer" />
            <label className="check">
              <input type="checkbox" checked={onlyReady}
                     onChange={(e) => setOnlyReady(e.target.checked)} />
              configured only
            </label>
          </div>

          {!data ? <Loading /> : (
            <div className="card">
              <table>
                <thead>
                  <tr><th>Model</th><th>Tier</th><th>Quality</th><th>Price</th>
                      <th>Identity</th><th>Ready</th></tr>
                </thead>
                <tbody>
                  {models.map((m) => (
                    <tr key={m.id}>
                      <td>
                        <div><strong>{m.display_name}</strong></div>
                        <div className="mono tiny dim">{m.id}</div>
                        {m.description && (
                          <div className="tiny muted" style={{ maxWidth: 380, marginTop: 3 }}>
                            {m.description}
                          </div>
                        )}
                      </td>
                      <td><span className={`badge ${TIER_TONE[m.tier] || ''}`}>{m.tier}</span></td>
                      <td className="small">{'★'.repeat(m.quality)}</td>
                      <td className="small mono">{priceOf(m)}</td>
                      <td>
                        {m.capabilities.reference_images
                          ? <span className="badge badge-success">
                              {m.capabilities.max_reference_images} refs
                            </span>
                          : <span className="tiny dim">—</span>}
                      </td>
                      <td>
                        {m.available
                          ? <span className="badge badge-success">yes</span>
                          : <span className="tiny dim">no key</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {view === 'routing' && (
        !profiles ? <Loading /> : (
          <div className="stack">
            {profiles.profiles.map((p) => (
              <div key={p.name} className="card">
                <div className="row-between">
                  <div>
                    <div className="row" style={{ gap: 8 }}>
                      <strong>{p.label}</strong>
                      {p.available_to_plan
                        ? <span className="badge badge-success">on your plan</span>
                        : <span className="badge">upgrade required</span>}
                    </div>
                    <div className="small muted" style={{ marginTop: 4 }}>{p.description}</div>
                  </div>
                </div>
                <div className="grid grid-2" style={{ marginTop: 12 }}>
                  {['showrunner', 'keyframe', 'shot_video', 'dialogue'].map((role) => (
                    <div key={role}>
                      <div className="stat-label">{role.replace(/_/g, ' ')}</div>
                      <div className="chain">
                        {(p.resolved_chain[role] || []).map((m, i) => (
                          <div key={m} className={i === 0 ? 'head' : ''}>
                            {i === 0 ? '' : '↳ '}{m}
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )
      )}
    </div>
  )
}

function priceOf(m) {
  const p = m.pricing
  if (p.usd_per_second) return `$${p.usd_per_second.toFixed(3)}/s`
  if (p.usd_per_image) return `$${p.usd_per_image.toFixed(3)}/img`
  if (p.usd_per_1k_chars) return `$${p.usd_per_1k_chars.toFixed(3)}/1k`
  if (p.usd_per_1m_output) return `$${p.usd_per_1m_input}/$${p.usd_per_1m_output} per M`
  return 'free'
}
