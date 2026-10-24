// 管理端 API：人工工单、会话详情与 AI 运行审计。

export const CUSTOMER_SERVICE_URL =
  import.meta.env.VITE_CUSTOMER_SERVICE_URL || 'http://127.0.0.1:8010'
export const LEGAL_SERVICE_URL =
  import.meta.env.VITE_LEGAL_SERVICE_URL || 'http://127.0.0.1:8001'
export const AI_SERVICE_URL = import.meta.env.VITE_AI_SERVICE_URL || 'http://127.0.0.1:8002'

async function parseResponse(response) {
  if (response.status === 204) return null
  const body = await response.json().catch(() => ({ detail: response.statusText }))
  if (!response.ok) {
    const detail = body.detail
    const error = new Error(
      (typeof detail === 'string' ? detail : detail?.message) || body.message || response.statusText,
    )
    error.status = response.status
    error.code = body.code || detail?.code
    throw error
  }
  return body
}

async function request(baseUrl, path, token, options = {}) {
  return parseResponse(
    await fetch(`${baseUrl}${path}`, {
      ...options,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
        ...(options.headers || {}),
      },
    }),
  )
}

export async function login(userId) {
  const role = userId.startsWith('admin_') ? 'admin' : 'agent'
  return parseResponse(
    await fetch(`${LEGAL_SERVICE_URL}/api/v1/auth/demo-token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: userId, role }),
    }),
  )
}

export const staffApi = {
  handoffs: (token) => request(CUSTOMER_SERVICE_URL, '/api/v1/handoffs', token),
  conversation: (token, id) =>
    request(CUSTOMER_SERVICE_URL, `/api/v1/conversations/${encodeURIComponent(id)}`, token),
  accept: (token, id) =>
    request(CUSTOMER_SERVICE_URL, `/api/v1/handoffs/${encodeURIComponent(id)}/accept`, token, {
      method: 'POST',
    }),
  reply: (token, id, messageId, text) =>
    request(CUSTOMER_SERVICE_URL, `/api/v1/handoffs/${encodeURIComponent(id)}/messages`, token, {
      method: 'POST',
      body: JSON.stringify({ message_id: messageId, text }),
    }),
  resolve: (token, id, note) =>
    request(CUSTOMER_SERVICE_URL, `/api/v1/handoffs/${encodeURIComponent(id)}/resolve`, token, {
      method: 'POST',
      body: JSON.stringify({ outcome: 'RESOLVED', resolution_note: note || null }),
    }),
  serviceMetrics: (token) => request(CUSTOMER_SERVICE_URL, '/api/v1/admin/metrics', token),
}

export const aiApi = {
  runs: (token, state = '') =>
    request(
      AI_SERVICE_URL,
      `/api/v1/runs${state ? `?state=${encodeURIComponent(state)}` : ''}`,
      token,
    ),
  run: (token, id) => request(AI_SERVICE_URL, `/api/v1/runs/${encodeURIComponent(id)}`, token),
  metrics: (token) => request(AI_SERVICE_URL, '/api/v1/admin/metrics', token),
  runtime: (token) => request(AI_SERVICE_URL, '/api/v1/admin/runtime', token),
}

export function createRealtimeConnection(token, callbacks = {}) {
  let socket
  let stopped = false
  let retryTimer
  let attempts = 0
  const url = `${CUSTOMER_SERVICE_URL.replace(/^http/, 'ws')}/api/v1/realtime`

  const connect = () => {
    if (stopped) return
    callbacks.onState?.(attempts ? 'reconnecting' : 'connecting')
    socket = new WebSocket(url)
    socket.addEventListener('open', () =>
      socket.send(JSON.stringify({ type: 'authenticate', token })),
    )
    socket.addEventListener('message', (browserEvent) => {
      let event
      try {
        event = JSON.parse(browserEvent.data)
      } catch {
        return
      }
      if (event.event_type === 'connected') {
        attempts = 0
        callbacks.onState?.('connected')
        callbacks.onConnected?.()
      } else {
        callbacks.onEvent?.(event)
      }
    })
    socket.addEventListener('close', () => {
      if (stopped) return callbacks.onState?.('closed')
      callbacks.onState?.('reconnecting')
      window.clearTimeout(retryTimer)
      retryTimer = window.setTimeout(connect, Math.min(1000 * 2 ** attempts++, 10000))
    })
    socket.addEventListener('error', () => socket?.close())
  }
  connect()
  return {
    close() {
      stopped = true
      window.clearTimeout(retryTimer)
      socket?.close(1000, 'client closed')
    },
  }
}

export function newId(prefix) {
  return `${prefix}_${Date.now()}_${Math.random().toString(16).slice(2, 10)}`
}
