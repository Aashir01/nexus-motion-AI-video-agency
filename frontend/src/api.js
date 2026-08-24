/**
 * Thin API client. Tokens live in localStorage; a 401 clears them and bounces
 * to the login screen rather than leaving the UI in a half-authenticated state.
 */
const BASE = import.meta.env.VITE_API_BASE || '/api/v1'
const TOKEN_KEY = 'nexus.tokens'

export function getTokens() {
  try {
    return JSON.parse(localStorage.getItem(TOKEN_KEY) || 'null')
  } catch {
    return null
  }
}

export function setTokens(tokens) {
  if (tokens) localStorage.setItem(TOKEN_KEY, JSON.stringify(tokens))
  else localStorage.removeItem(TOKEN_KEY)
  window.dispatchEvent(new Event('nexus:auth'))
}

export function isAuthed() {
  return Boolean(getTokens()?.access_token)
}

export class ApiError extends Error {
  constructor(message, status, code, detail) {
    super(message)
    this.status = status
    this.code = code
    this.detail = detail
  }
}

async function request(path, { method = 'GET', body, auth = true, signal } = {}) {
  const headers = { 'Content-Type': 'application/json' }
  const tokens = getTokens()
  if (auth && tokens?.access_token) headers.Authorization = `Bearer ${tokens.access_token}`

  const res = await fetch(`${BASE}${path}`, {
    method,
    headers,
    signal,
    body: body === undefined ? undefined : JSON.stringify(body),
  })

  if (res.status === 401 && auth) {
    setTokens(null)
    throw new ApiError('Your session expired. Sign in again.', 401, 'unauthorized')
  }
  if (res.status === 204) return null

  const text = await res.text()
  const payload = text ? JSON.parse(text) : null
  if (!res.ok) {
    const err = payload?.error || {}
    throw new ApiError(err.message || res.statusText, res.status, err.code, err.detail)
  }
  return payload
}

export const api = {
  signup: (body) => request('/auth/signup', { method: 'POST', body, auth: false }),
  login: (body) => request('/auth/login', { method: 'POST', body, auth: false }),
  me: () => request('/auth/me'),
  apiKeys: () => request('/auth/api-keys'),
  createApiKey: (name) => request('/auth/api-keys', { method: 'POST', body: { name } }),
  revokeApiKey: (id) => request(`/auth/api-keys/${id}`, { method: 'DELETE' }),

  listSeries: () => request('/series'),
  createSeries: (body) => request('/series', { method: 'POST', body }),
  getSeries: (id) => request(`/series/${id}`),
  getBible: (id) => request(`/series/${id}/bible`),
  deleteSeries: (id) => request(`/series/${id}`, { method: 'DELETE' }),
  listEpisodes: (id) => request(`/series/${id}/episodes`),
  getEpisodePlan: (id) => request(`/episodes/${id}/plan`),

  quote: (body) => request('/productions/quote', { method: 'POST', body }),
  startProduction: (body) => request('/productions', { method: 'POST', body }),
  listJobs: () => request('/productions'),
  getJob: (id) => request(`/productions/${id}`),
  cancelJob: (id) => request(`/productions/${id}/cancel`, { method: 'POST' }),

  models: (query = '') => request(`/models${query}`),
  profiles: () => request('/models/routing/profiles'),
  health: () => request('/models/routing/health'),

  plans: () => request('/billing/plans', { auth: false }),
  account: () => request('/billing/account'),
  credits: () => request('/billing/credits'),
  usage: (days = 30) => request(`/billing/usage?days=${days}`),
  checkout: (tier) => request('/billing/checkout', { method: 'POST', body: { tier } }),
  topUp: (credits) => request('/billing/top-up', { method: 'POST', body: { credits } }),
  portal: () => request('/billing/portal', { method: 'POST' }),
}

/**
 * SSE cannot carry an Authorization header, so progress is read with fetch +
 * a streaming body reader instead of EventSource.
 */
export function streamJob(jobId, onEvent, onEnd) {
  const controller = new AbortController()
  const tokens = getTokens()

  ;(async () => {
    try {
      const res = await fetch(`${BASE}/productions/${jobId}/events`, {
        headers: { Authorization: `Bearer ${tokens?.access_token || ''}` },
        signal: controller.signal,
      })
      if (!res.ok || !res.body) throw new Error(`stream failed: ${res.status}`)

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const chunks = buffer.split('\n\n')
        buffer = chunks.pop() || ''
        for (const chunk of chunks) {
          if (chunk.startsWith(':')) continue
          if (chunk.startsWith('event: end')) {
            onEnd?.()
            controller.abort()
            return
          }
          const line = chunk.split('\n').find((l) => l.startsWith('data: '))
          if (!line) continue
          try {
            onEvent(JSON.parse(line.slice(6)))
          } catch {
            /* a partial frame; the next read completes it */
          }
        }
      }
      onEnd?.()
    } catch (err) {
      if (err.name !== 'AbortError') onEnd?.(err)
    }
  })()

  return () => controller.abort()
}
