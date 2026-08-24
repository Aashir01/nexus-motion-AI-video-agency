import React, { useEffect, useState } from 'react'
import { api } from '../api'
import { Alert, Loading, Stat, formatDate, formatUsd } from '../components/ui'

export default function Billing() {
  const [account, setAccount] = useState(null)
  const [plans, setPlans] = useState([])
  const [entries, setEntries] = useState([])
  const [usage, setUsage] = useState(null)
  const [keys, setKeys] = useState([])
  const [newKey, setNewKey] = useState('')
  const [error, setError] = useState('')

  async function load() {
    try {
      const [a, p, c, u] = await Promise.all([
        api.account(), api.plans(), api.credits(), api.usage(30),
      ])
      setAccount(a); setPlans(p.plans); setEntries(c); setUsage(u)
      api.apiKeys().then(setKeys).catch(() => setKeys([]))
    } catch (err) {
      setError(err.message)
    }
  }

  useEffect(() => { load() }, [])

  async function upgrade(tier) {
    setError('')
    try {
      const { checkout_url } = await api.checkout(tier)
      window.location.href = checkout_url
    } catch (err) {
      setError(err.message)
    }
  }

  async function createKey() {
    try {
      const created = await api.createApiKey('studio key')
      setNewKey(created.secret)
      setKeys(await api.apiKeys())
    } catch (err) {
      setError(err.message)
    }
  }

  if (!account) return <Loading />

  return (
    <div className="content">
      <div className="page-head">
        <div>
          <h1>Billing & usage</h1>
          <p>Credits are the production currency: 100 credits = $1 of retail
             capacity. Every job reserves its quote up front and refunds whatever
             it did not spend.</p>
        </div>
      </div>

      <Alert kind="error">{error}</Alert>
      {newKey && (
        <Alert kind="success">
          Your new API key — copy it now, it will not be shown again:
          <div className="mono small" style={{ marginTop: 6, wordBreak: 'break-all' }}>{newKey}</div>
        </Alert>
      )}

      <div className="grid grid-4" style={{ marginBottom: 20 }}>
        <Stat label="Plan" value={account.plan.name} sub={`$${account.plan.price_usd_month}/mo`} />
        <Stat label="Credits" value={account.credit_balance.toLocaleString()}
              sub={`≈ $${account.credit_balance_usd.toFixed(2)}`} />
        <Stat label="Spend, 30 days" value={formatUsd(usage?.total_cost_usd ?? 0)}
              sub={`${usage?.total_calls ?? 0} model calls`} />
        <Stat label="Fallback rate"
              value={`${Math.round((usage?.fallback_rate ?? 0) * 100)}%`}
              sub="calls served by a backup model" />
      </div>

      {!account.billing_enabled && (
        <Alert kind="info">
          Billing is not configured on this deployment — set
          <code> STRIPE_SECRET_KEY</code> and <code>BILLING_ENABLED=true</code> to take payments.
          Plans and credit accounting work regardless.
        </Alert>
      )}

      <h2 style={{ margin: '4px 0 12px' }}>Plans</h2>
      <div className="grid grid-3">
        {plans.map((p) => (
          <div key={p.tier} className={`price-card ${p.tier === 'studio' ? 'featured' : ''}`}>
            <div className="row-between">
              <strong>{p.name}</strong>
              {p.tier === account.plan.tier && <span className="badge badge-accent">current</span>}
            </div>
            <div className="price-amount">
              {p.tier === 'enterprise' ? 'Custom' : `$${p.price_usd_month}`}
              {p.tier !== 'enterprise' && <span className="muted" style={{ fontSize: 14 }}>/mo</span>}
            </div>
            <div className="small muted">
              {p.monthly_credits ? `${p.monthly_credits.toLocaleString()} credits/mo` : 'volume pricing'}
            </div>
            <ul className="feature-list">
              {p.features.map((f) => <li key={f}>{f}</li>)}
            </ul>
            {p.tier === account.plan.tier ? (
              <button className="btn btn-block" disabled>Current plan</button>
            ) : p.tier === 'enterprise' ? (
              <a className="btn btn-block" href="mailto:sales@example.com">Contact sales</a>
            ) : p.tier === 'free' ? (
              <button className="btn btn-block" disabled>Included</button>
            ) : (
              <button className="btn btn-primary btn-block"
                      disabled={!account.billing_enabled}
                      onClick={() => upgrade(p.tier)}>
                Upgrade
              </button>
            )}
          </div>
        ))}
      </div>

      <div className="grid grid-2" style={{ marginTop: 20, alignItems: 'start' }}>
        <div className="card">
          <div className="card-head"><h2>Credit history</h2></div>
          {entries.length === 0 ? <div className="small muted">No activity yet.</div> : (
            <table>
              <thead><tr><th>When</th><th>Reason</th>
                <th style={{ textAlign: 'right' }}>Δ</th>
                <th style={{ textAlign: 'right' }}>Balance</th></tr></thead>
              <tbody>
                {entries.slice(0, 12).map((e) => (
                  <tr key={e.id}>
                    <td className="small muted">{formatDate(e.created_at)}</td>
                    <td className="small">{e.reason}</td>
                    <td className="small mono" style={{ textAlign: 'right',
                          color: e.delta >= 0 ? 'var(--success)' : 'var(--text)' }}>
                      {e.delta >= 0 ? '+' : ''}{e.delta.toLocaleString()}
                    </td>
                    <td className="small mono" style={{ textAlign: 'right' }}>
                      {e.balance_after.toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="card">
          <div className="card-head">
            <h2>API keys</h2>
            <button className="btn btn-sm" onClick={createKey}
                    disabled={!account.plan.api_access}>New key</button>
          </div>
          {!account.plan.api_access && (
            <div className="small muted">API access starts on the Starter plan.</div>
          )}
          {keys.map((k) => (
            <div key={k.id} className="row-between" style={{ padding: '7px 0',
                    borderBottom: '1px solid var(--border)' }}>
              <div>
                <div className="small"><strong>{k.name}</strong></div>
                <div className="mono tiny dim">{k.prefix}…</div>
              </div>
              <div className="row">
                {k.revoked && <span className="badge badge-danger">revoked</span>}
                {!k.revoked && (
                  <button className="btn btn-sm btn-danger"
                          onClick={async () => { await api.revokeApiKey(k.id); setKeys(await api.apiKeys()) }}>
                    Revoke
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>

      {usage?.by_model && Object.keys(usage.by_model).length > 0 && (
        <div className="card" style={{ marginTop: 14 }}>
          <div className="card-head"><h2>Spend by model, last 30 days</h2></div>
          <table>
            <thead><tr><th>Model</th><th style={{ textAlign: 'right' }}>Cost</th></tr></thead>
            <tbody>
              {Object.entries(usage.by_model).map(([m, c]) => (
                <tr key={m}>
                  <td className="mono small">{m}</td>
                  <td className="small" style={{ textAlign: 'right' }}>{formatUsd(c)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
