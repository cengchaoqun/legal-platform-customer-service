// 用户端共享状态与动作。视图只负责渲染，业务编排集中在这里，
// 便于统一处理幂等、重试、断线补拉与人工会话状态。

import { computed, nextTick, reactive } from 'vue'
import {
  createRealtimeConnection,
  customerApi,
  legalApi,
  listDemoUsers,
  login,
  newId,
} from './api'

export const state = reactive({
  token: localStorage.getItem('legal_customer_token') || '',
  principal: null,
  users: [],
  selectedUser: localStorage.getItem('legal_customer_id') || 'user_001',
  route: `${location.pathname}${location.search}`,
  loading: false,
  error: '',
  notice: '',

  conversationId: null,
  mode: 'AI',
  handoffStatus: null,
  processing: false,
  wsState: 'closed',
  messages: [],

  history: [],
  orders: [],
  plans: [],
  planQuery: '',
  planType: '',
  plansLoading: false,

  order: null,
  progress: null,
  invoice: null,
  afterSales: [],
  orderLoading: false,

  // 写操作状态：idle / submitting / success / failed / unknown
  writeState: 'idle',
  writeResult: null,
  writeError: '',
  idempotencyKey: '',
  lastFailedMessage: null,
})

let realtime
let scrollTarget = null

export const path = computed(() => state.route.split('?')[0])
export const query = computed(() => new URLSearchParams(state.route.split('?')[1] || ''))
export const orderMatch = computed(() =>
  path.value.match(/^\/me\/orders\/([^/]+)(?:\/(refund|invoice))?$/),
)
export const orderId = computed(() => orderMatch.value?.[1] || '')
export const operation = computed(() => orderMatch.value?.[2] || '')
export const modeLabel = computed(
  () =>
    ({
      AI: '智能客服',
      QUEUED: '等待人工接入',
      HUMAN: '人工服务中',
      CLOSED: '已结束',
    })[state.mode] || state.mode,
)
export const historyGroups = computed(() => {
  const groups = new Map()
  for (const message of state.history) {
    if (!groups.has(message.conversation_id)) {
      groups.set(message.conversation_id, {
        id: message.conversation_id,
        startedAt: message.conversation_started_at,
        messages: [],
      })
    }
    groups.get(message.conversation_id).messages.push(message)
  }
  return [...groups.values()]
    .map((group) => ({
      ...group,
      messages: group.messages.sort((left, right) => left.sequence - right.sequence),
      preview: textOf(group.messages[group.messages.length - 1]),
    }))
    .sort((left, right) => new Date(right.startedAt) - new Date(left.startedAt))
})

export function textOf(message) {
  const content = message?.content || {}
  if (content.text) return content.text
  if (content.object_type === 'order') return `已选择订单 ${content.object_id}`
  if (content.object_type === 'plan') return `已发送套餐 ${content.object_id}`
  return '暂不支持的消息'
}

export function setScrollTarget(element) {
  scrollTarget = element
}

function scrollToBottom() {
  nextTick(() => {
    if (scrollTarget) scrollTarget.scrollTop = scrollTarget.scrollHeight
  })
}

export function clearFeedback() {
  state.error = ''
  state.notice = ''
}

export function navigate(to) {
  clearFeedback()
  history.pushState({}, '', to)
  state.route = `${location.pathname}${location.search}`
  loadRoute()
}

function upsertMessage(message) {
  const key = message.message_id || `${message.sequence}`
  const index = state.messages.findIndex((item) => item.message_id === key)
  if (index >= 0) state.messages[index] = { ...state.messages[index], ...message }
  else state.messages.push(message)
}

export async function bootstrap() {
  state.loading = true
  clearFeedback()
  try {
    state.users = await listDemoUsers().catch(() => [])
    if (!state.users.some((user) => user.role === 'customer')) {
      state.users = [{ id: 'user_001', name: '张岚', role: 'customer' }]
    }
    await establishSession()
  } finally {
    state.loading = false
  }
}

export async function switchUser(userId) {
  state.selectedUser = userId
  await establishSession()
}

async function establishSession() {
  clearFeedback()
  try {
    const result = await login(state.selectedUser)
    state.token = result.access_token
    state.principal = result.principal
    localStorage.setItem('legal_customer_token', state.token)
    localStorage.setItem('legal_customer_id', state.selectedUser)

    realtime?.close()
    realtime = createRealtimeConnection(state.token, {
      onState: (value) => {
        state.wsState = value
      },
      onConnected: ({ reconnected }) => {
        if (reconnected) {
          syncMissedMessages().catch((cause) => {
            state.error = `断线补拉失败：${cause.message}`
          })
        }
      },
      onEvent: handleRealtimeEvent,
    })
    await loadRoute()
  } catch (cause) {
    state.error = cause.message
  }
}

function handleRealtimeEvent(event) {
  if (event.event_type === 'message_created') {
    const message = event.event_data
    if (message.conversation_id === state.conversationId) {
      upsertMessage(message)
      scrollToBottom()
      if (message.role === 'ai' || message.role === 'human') state.processing = false
    }
  }
  if (event.event_type === 'handoff_changed') {
    state.processing = false
    if (event.event_data?.conversation_mode) state.mode = event.event_data.conversation_mode
    if (event.event_data?.status) state.handoffStatus = event.event_data.status
  }
}

export async function loadRoute() {
  if (!state.token) return
  clearFeedback()
  const current = path.value
  try {
    if (current === '/' || current === '/chat') await loadChat()
    else if (current === '/plans') await loadPlans()
    else if (current === '/me') await loadOrders()
    else if (current === '/history') await loadHistory()
    else if (orderId.value) await loadOrderDetail(orderId.value)
    else navigate('/chat')
  } catch (cause) {
    state.error = cause.message
  }
}

// ------------------------------------------------------------------ 聊天
async function loadChat() {
  const [conversation, history] = await Promise.all([
    customerApi.currentConversation(state.token),
    customerApi.history(state.token),
  ])
  state.conversationId = conversation.id
  state.mode = conversation.mode
  state.handoffStatus = conversation.handoff_status
  state.processing = Boolean(conversation.is_processing)
  state.messages = history
    .filter((message) => message.conversation_id === conversation.id)
    .map((message) => ({ ...message }))
  scrollToBottom()
}

export async function loadHistory() {
  state.history = await customerApi.history(state.token)
}

async function syncMissedMessages() {
  if (!state.conversationId) return
  const lastSequence = state.messages.reduce(
    (latest, message) => Math.max(latest, message.sequence || 0),
    0,
  )
  const missed = await customerApi.history(state.token, lastSequence)
  for (const message of missed) {
    if (message.conversation_id === state.conversationId) upsertMessage(message)
  }
  scrollToBottom()
}

export async function sendText(text) {
  const content = String(text || '').trim()
  if (!content) return
  await sendPayload('text', { text: content })
}

export async function sendObject(objectType, objectId, queryText = '') {
  const content = { object_type: objectType, object_id: objectId }
  if (queryText) content.query_text = queryText
  await sendPayload('object', content)
}

async function sendPayload(type, content, messageId = null) {
  const id = messageId || newId('msg')
  const optimistic = {
    message_id: id,
    conversation_id: state.conversationId,
    role: 'user',
    type,
    content,
    created_at: new Date().toISOString(),
    delivery_status: 'sending',
  }
  upsertMessage(optimistic)
  scrollToBottom()
  if (state.mode === 'AI') state.processing = true

  try {
    const result = await customerApi.send(state.token, {
      message_id: id,
      type,
      content,
    })
    state.conversationId = result.conversation_id
    state.mode = result.mode
    state.handoffStatus = result.handoff_created ? 'waiting' : state.handoffStatus
    const stored = state.messages.find((item) => item.message_id === id)
    if (stored) stored.delivery_status = 'sent'
    state.lastFailedMessage = null
    if (result.mode !== 'AI') state.processing = false
  } catch (cause) {
    if (state.mode === 'AI') state.processing = false
    const stored = state.messages.find((item) => item.message_id === id)
    if (stored) stored.delivery_status = 'failed'
    state.lastFailedMessage = { id, type, content }
    state.error = `消息发送失败：${cause.message}。可以重试，重试会复用原消息编号。`
  }
}

export async function retryLastMessage() {
  if (!state.lastFailedMessage) return
  const { id, type, content } = state.lastFailedMessage
  state.messages = state.messages.filter((item) => item.message_id !== id)
  await sendPayload(type, content, id)
}

export async function requestHuman() {
  await sendText('转人工客服')
}

// ------------------------------------------------------------------ 套餐
export async function loadPlans() {
  state.plansLoading = true
  try {
    state.plans = await legalApi.plans(state.token, state.planQuery, state.planType)
  } finally {
    state.plansLoading = false
  }
}

export async function searchPlans() {
  await loadPlans()
}

export function consultPlan(planId) {
  navigate('/chat')
  sendObject('plan', planId, '请介绍这个套餐的权益')
}

// ------------------------------------------------------------------ 订单
export async function loadOrders() {
  state.orders = await legalApi.orders(state.token)
}

export async function loadOrderDetail(id) {
  state.orderLoading = true
  clearFeedback()
  try {
    const [order, progress, invoice, afterSales] = await Promise.all([
      legalApi.order(state.token, id),
      legalApi.progress(state.token, id),
      legalApi.invoice(state.token, id),
      legalApi.afterSales(state.token, id),
    ])
    state.order = order
    state.progress = progress
    state.invoice = invoice
    state.afterSales = afterSales
    if (operation.value) {
      state.writeState = 'idle'
      state.writeResult = null
      state.writeError = ''
      state.idempotencyKey = newId(operation.value)
    }
  } finally {
    state.orderLoading = false
  }
}

export function consultOrder(id) {
  navigate('/chat')
  sendObject('order', id, '请帮我看看这笔订单')
}

async function runWrite(action) {
  state.writeState = 'submitting'
  state.writeError = ''
  state.writeResult = null
  try {
    const result = await action()
    state.writeState = 'success'
    state.writeResult = result
    return result
  } catch (cause) {
    if (!cause.status) {
      // 网络层失败：结果未知，保留幂等键供用户核对后重试
      state.writeState = 'unknown'
      state.writeError = '网络中断，暂时无法确认是否已受理。可以点击“核对结果”查询原申请。'
      return null
    }
    if (cause.status === 409) {
      state.writeState = 'failed'
      state.writeError = `${cause.message}。请刷新订单后再试。`
      await loadOrderDetail(orderId.value).catch(() => {})
      return null
    }
    state.writeState = 'failed'
    state.writeError = cause.message
    return null
  }
}

export async function submitCancel() {
  const key = state.idempotencyKey || (state.idempotencyKey = newId('cancel'))
  const result = await runWrite(() => legalApi.cancel(state.token, orderId.value, key))
  if (result) await loadOrderDetail(orderId.value)
  return result
}

export async function submitRefund({ reason, amount }) {
  const key = state.idempotencyKey || (state.idempotencyKey = newId('refund'))
  const result = await runWrite(() =>
    legalApi.applyAfterSale(
      state.token,
      orderId.value,
      { kind: 'REFUND', reason, requested_amount: amount || null },
      key,
    ),
  )
  if (result) {
    await loadOrderDetail(orderId.value).catch(() => {})
  }
  return result
}

export async function submitInvoice(payload) {
  const key = state.idempotencyKey || (state.idempotencyKey = newId('invoice'))
  const result = await runWrite(() => legalApi.applyInvoice(state.token, orderId.value, payload, key))
  if (result) {
    await loadOrderDetail(orderId.value).catch(() => {})
  }
  return result
}

export async function verifyWriteResult() {
  // 结果未知时按业务接口重新核对，不重复提交
  state.writeState = 'submitting'
  try {
    if (operation.value === 'refund') {
      const records = await legalApi.afterSales(state.token, orderId.value)
      const latest = records[0]
      state.writeState = latest ? 'success' : 'failed'
      state.writeResult = latest || null
      if (!latest) state.writeError = '没有查询到新的申请记录，可以重新提交。'
    } else if (operation.value === 'invoice') {
      const invoice = await legalApi.invoice(state.token, orderId.value)
      state.writeState = invoice.status === 'NONE' ? 'failed' : 'success'
      state.writeResult = invoice
      if (invoice.status === 'NONE') state.writeError = '没有查询到开票申请，可以重新提交。'
    }
  } catch (cause) {
    state.writeState = 'failed'
    state.writeError = cause.message
  }
}

export function resetWriteState() {
  state.writeState = 'idle'
  state.writeResult = null
  state.writeError = ''
  state.idempotencyKey = newId(operation.value || 'write')
}

export function teardown() {
  realtime?.close()
}
