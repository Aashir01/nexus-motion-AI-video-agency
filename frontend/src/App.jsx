import React, { useEffect, useState } from 'react'
import { api, isAuthed, setTokens } from './api'
import Auth from './pages/Auth'
import Dashboard from './pages/Dashboard'
import SeriesDetail from './pages/SeriesDetail'
import Production from './pages/Production'
import Models from './pages/Models'
import Billing from './pages/Billing'

/** Hash routing — no dependency, and it survives a static deploy on any host. */
function useHashRoute() {
  const [route, setRoute] = useState(window.location.hash.slice(1) || '/')
  useEffect(() => {
    const onChange = () => setRoute(window.location.hash.slice(1) || '/')
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  const navigate = (path) => { window.location.hash = path }
  return [route, navigate]
}

const NAV = [
  { path: '/', label: 'Studio', icon: '◧' },
  { path: '/models', label: 'Models', icon: '◇' },
  { path: '/billing', label: 'Billing', icon: '◈' },
]

export default function App() {
  const [authed, setAuthed] = useState(isAuthed())
  const [me, setMe] = useState(null)
  const [route, navigate] = useHashRoute()

  useEffect(() => {
    const onAuth = () => {
      const next = isAuthed()
      setAuthed(next)
      if (!next) setMe(null)
    }
    window.addEventListener('nexus:auth', onAuth)
    return () => window.removeEventListener('nexus:auth', onAuth)
  }, [])

  useEffect(() => {
    if (authed) api.me().then(setMe).catch(() => {})
  }, [authed, route])

  if (!authed) return <Auth />

  let page
  const seriesMatch = route.match(/^\/series\/([^/]+)/)
  const productionMatch = route.match(/^\/production\/([^/]+)/)

  if (seriesMatch) page = <SeriesDetail id={seriesMatch[1]} navigate={navigate} />
  else if (productionMatch) page = <Production id={productionMatch[1]} />
  else if (route.startsWith('/models')) page = <Models />
  else if (route.startsWith('/billing')) page = <Billing />
  else page = <Dashboard navigate={navigate} />

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">◈</span> Nexus Motion
        </div>
        {NAV.map((item) => (
          <a key={item.path} href={`#${item.path}`}
             className={`nav-item ${isActive(route, item.path) ? 'active' : ''}`}>
            <span style={{ width: 16 }}>{item.icon}</span> {item.label}
          </a>
        ))}
        <div className="spacer" />
        {me && (
          <>
            <div className="nav-section">Account</div>
            <div style={{ padding: '4px 10px' }}>
              <div className="small" style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {me.email}
              </div>
              <div className="tiny dim">
                {me.org_name} · {me.plan}
              </div>
              <div className="tiny" style={{ color: 'var(--accent)', marginTop: 3 }}>
                {me.credit_balance.toLocaleString()} credits
              </div>
            </div>
            <button className="nav-item" onClick={() => setTokens(null)}>
              <span style={{ width: 16 }}>⏻</span> Sign out
            </button>
          </>
        )}
      </aside>

      <main className="main">
        <div className="topbar">
          <div className="small muted">
            {route === '/' ? 'Studio' : route.replace(/^\//, '').split('/')[0]}
          </div>
          <a className="btn btn-sm" href="/docs" target="_blank" rel="noreferrer">API docs</a>
        </div>
        {page}
      </main>
    </div>
  )
}

function isActive(route, path) {
  if (path === '/') return route === '/' || route.startsWith('/series') || route.startsWith('/production')
  return route.startsWith(path)
}
