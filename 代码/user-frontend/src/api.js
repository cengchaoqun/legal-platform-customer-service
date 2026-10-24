// 用户端 API 封装：会话、业务查询与用户确认后的写操作。
// 约定：写请求必须携带稳定的幂等键；消息重试必须复用原 message_id。

export const CUSTOMER_SERVICE_URL =
  import.meta.env.VITE_CUSTOMER_SERVICE_URL || 'http://127.0.0.1:8010'
export const LEGAL_SERVICE_URL =
  import.meta.env.VITE_LEGAL_SERVICE_URL || 'http://127.0.0.1:8001'

async function parseResponse(response) {
  if (response.status === 204) return null
  const body = await response.json().catch(() => ({ detail: response.statusText }))
  if (!response.ok) {
    const detail = body.detail
    const message =
      (typeof detail === 'string' ? detail : detail?.message) ||
      body.message ||
      response.statusText
    const error = new Error(message)
    error.status = response.status
    error.code = body.code || detail?.code
    throw error
  }
  return body
}

function unwrap(result) {
  if (result && result.success === false) {
    const error = new Error(result.message || '业务请求未受理')
    error.code = result.code
    throw error
  }
  return result?.data ?? result
}

export async function login(userId) {
  return parseResponse(
    await fetch(`${LEGAL_SERVICE_URL}/api/v1/auth/demo-token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: userId, role: 'customer' }),
    }),
  )
}

export async function listDemoUsers() {
  return parseResponse(await fetch(`${LEGAL_SERVICE_URL}/api/v1/demo/users`))
}

async function request(baseUrl, path, token, options = {}) {
  const response = await fetch(`${baseUrl}${path}`, {
    ...options,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...(options.headers || {}),
    },
  })
  return parseResponse(response)
}

export const customerApi = {
  currentConversation: (token) =>
    request(CUSTOMER_SERVICE_URL, '/api/v1/conversations/current', token, { method: 'POST' }),
  history: (token, afterSequence = null) =>
    request(
      CUSTOMER_SERVICE_URL,
      `/api/v1/chat/history${afterSequence == null ? '' : `?after_sequence=${afterSequence}`}`,
      token,
    ),
  send: (token, payload) =>
    request(CUSTOMER_SERVICE_URL, '/api/v1/chat/messages', token, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
}

export const legalApi = {
  plans: (token, query = '', serviceType = '') => {
    const params = new URLSearchParams()
    if (query) params.set('query', query)
    if (serviceType) params.set('service_type', serviceType)
    const suffix = params.toString() ? `?${params}` : ''
    return request(LEGAL_SERVICE_URL, `/api/v1/plans${suffix}`, token).then(unwrap)
  },
  orders: (token) => request(LEGAL_SERVICE_URL, '/api/v1/orders', token).then(unwrap),
  plan: (token, id) =>
    request(LEGAL_SERVICE_URL, `/api/v1/plans/${encodeURIComponent(id)}`, token).then(unwrap),
  order: (token, id) =>
    request(LEGAL_SERVICE_URL, `/api/v1/orders/${encodeURIComponent(id)}`, token).then(unwrap),
  progress: (token, id) =>
    request(
      LEGAL_SERVICE_URL,
      `/api/v1/orders/${encodeURIComponent(id)}/service-progress`,
      token,
    ).then(unwrap),
  invoice: (token, id) =>
    request(LEGAL_SERVICE_URL, `/api/v1/orders/${encodeURIComponent(id)}/invoice`, token).then(
      unwrap,
    ),
  afterSales: (token, orderId = '') =>
    request(
      LEGAL_SERVICE_URL,
      `/api/v1/after-sales${orderId ? `?order_id=${encodeURIComponent(orderId)}` : ''}`,
      token,
    ).then(unwrap),
  cancel: (token, id, key) =>
    request(LEGAL_SERVICE_URL, `/api/v1/orders/${encodeURIComponent(id)}/cancel`, token, {
      method: 'POST',
      headers: { 'Idempotency-Key': key },
      body: JSON.stringify({}),
    }).then(unwrap),
  applyInvoice: (token, id, payload, key) =>
    request(
      LEGAL_SERVICE_URL,
      `/api/v1/orders/${encodeURIComponent(id)}/invoice-applications`,
      token,
      {
        method: 'POST',
        headers: { 'Idempotency-Key': key },
        body: JSON.stringify(payload),
      },
    ).then(unwrap),
  applyAfterSale: (token, id, payload, key) =>
    request(LEGAL_SERVICE_URL, `/api/v1/orders/${encodeURIComponent(id)}/after-sales`, token, {
      method: 'POST',
      headers: { 'Idempotency-Key': key },
      body: JSON.stringify(payload),
    }).then(unwrap),
}

export function createRealtimeConnection(token, callbacks = {}) {
  let socket
  let stopped = false
  let retryTimer
  let attempts = 0
  let hasConnected = false
  const url = `${CUSTOMER_SERVICE_URL.replace(/^http/, 'ws')}/api/v1/realtime`

  const clearTimers = () => window.clearTimeout(retryTimer)
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
        const reconnected = hasConnected
        hasConnected = true
        attempts = 0
        callbacks.onState?.('connected')
        callbacks.onConnected?.({ reconnected })
      } else {
        callbacks.onEvent?.(event)
      }
    })
    socket.addEventListener('close', () => {
      clearTimers()
      if (stopped) return callbacks.onState?.('closed')
      callbacks.onState?.('reconnecting')
      retryTimer = window.setTimeout(connect, Math.min(1000 * 2 ** attempts++, 10000))
    })
    socket.addEventListener('error', () => socket?.close())
  }
  connect()
  return {
    close() {
      stopped = true
      clearTimers()
      socket?.close(1000, 'client closed')
    },
  }
}

export function newId(prefix) {
  return `${prefix}_${Date.now()}_${Math.random().toString(16).slice(2, 10)}`
}
