import React, { useState } from 'react'
import { api, setTokens } from '../api'
import { Alert } from '../components/ui'

export default function Auth() {
  const [mode, setMode] = useState('login')
  const [form, setForm] = useState({ email: '', password: '', organization_name: '' })
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const update = (key) => (e) => setForm({ ...form, [key]: e.target.value })

  async function submit(e) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      const tokens = mode === 'login'
        ? await api.login({ email: form.email, password: form.password })
        : await api.signup(form)
      setTokens(tokens)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-shell">
      <div className="auth-card">
        <div className="brand" style={{ padding: '0 0 18px' }}>
          <span className="brand-mark">◈</span> Nexus Motion
        </div>
        <h1 style={{ fontSize: 20 }}>
          {mode === 'login' ? 'Sign in to your studio' : 'Create your studio'}
        </h1>
        <p className="muted small" style={{ marginTop: 6, marginBottom: 20 }}>
          {mode === 'login'
            ? 'Pick up where your last production left off.'
            : 'Free tier includes 300 credits and 3-minute episodes — no card needed.'}
        </p>

        <Alert kind="error">{error}</Alert>

        <form onSubmit={submit}>
          {mode === 'signup' && (
            <div className="field">
              <label>Studio name</label>
              <input value={form.organization_name} onChange={update('organization_name')}
                     placeholder="Harbour Pictures" />
            </div>
          )}
          <div className="field">
            <label>Email</label>
            <input type="email" required value={form.email} onChange={update('email')}
                   placeholder="you@studio.com" autoComplete="email" />
          </div>
          <div className="field">
            <label>Password</label>
            <input type="password" required minLength={8} value={form.password}
                   onChange={update('password')} placeholder="At least 8 characters"
                   autoComplete={mode === 'login' ? 'current-password' : 'new-password'} />
          </div>
          <button className="btn btn-primary btn-block" disabled={busy}>
            {busy ? <span className="spinner" /> : mode === 'login' ? 'Sign in' : 'Create studio'}
          </button>
        </form>

        <div className="center small muted" style={{ marginTop: 16 }}>
          {mode === 'login' ? "Don't have an account? " : 'Already have one? '}
          <button className="btn-ghost" style={{ background: 'none', border: 0, color: 'var(--accent)', cursor: 'pointer', font: 'inherit' }}
                  onClick={() => { setMode(mode === 'login' ? 'signup' : 'login'); setError('') }}>
            {mode === 'login' ? 'Create one' : 'Sign in'}
          </button>
        </div>
      </div>
    </div>
  )
}
