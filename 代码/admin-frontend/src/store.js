// 管理端共享状态：工单工作台与运行审计。

import { computed, reactive } from 'vue'
import { aiApi, createRealtimeConnection, login, newId, staffApi } from './api'

export const state = reactive({
  token: localStorage.getItem('legal_staff_token') || '',
  principal: null,
  account: localStorage.getItem('legal_staff_id') || 'agent_001',
  section: 'handoffs',
  wsState: 'closed',
  loading: false,
  error: '',
  notice: '',

  handoffs: [],
  selectedId: null,
  conversation: null,
  conversationLoading: false,
  reply: '',
  resolving: false,

  runs: [],
  runsLoading: false,
  selectedRun: null,
  runFilter: '',
  metrics: { service: {}, ai: {} },
  runtime: null,
})

let realtime
let refreshTimer

export const isAdmin = computed(() => state.principal?.role === 'admin')
export const selectedHandoff = computed(
  () => state.handoffs.find((item) => item.id === state.selectedId) || null,
)
export const canManage = computed(
  () =>
    selectedHandoff.value?.status === 'active' &&
    selectedHandoff.value?.assigned_agent_id === state.principal?.user_id,
)

export function clearFeedback() {
  state.error = ''
  state.notice = ''
}

export async function authenticate() {
  clearFeedback()
  state.loading = true
  try {
    const result = await login(state.account)
    state.token = result.access_token
    state.principal = result.principal
    localStorage.setItem('legal_staff_token', state.token)
    localStorage.setItem('legal_staff_id', state.account)
    state.section = isAdmin.value ? 'runs' : 'handoffs'
    state.selectedId = null
    state.conversation = null

    realtime?.close()
    realtime = createRealtimeConnection(state.token, {
      onState: (value) => {
        state.wsState = value
      },
      onConnected: () => reloadCurrent(),
      onEvent: (event) => {
        if (event.event_type === 'message_created' || event.event_type === 'handoff_changed') {
          scheduleRefresh()
        }
      },
    })
    await loadSection()
  } catch (cause) {
    state.error = cause.message
  } finally {
    state.loading = false
  }
}

function scheduleRefresh() {
  window.clearTimeout(refreshTimer)
  refreshTimer = window.setTimeout(() => reloadCurrent().catch(() => {}), 200)
}

async function reloadCurrent() {
  // 断线重连后重新拉取授权工单与会话，前端按 message_id 合并
  await loadHandoffs()
  if (state.selectedId && state.conversation) {
    const current = state.handoffs.find((item) => item.id === state.selectedId)
    if (current) await openHandoff(current, { keepSelection: true })
    else {
      state.selectedId = null
      state.conversation = null
    }
  }
}

export async function switchSection(section) {
  if (section === 'runs' && !isAdmin.value) return
  state.section = section
  clearFeedback()
  await loadSection()
}

export async function loadSection() {
  if (state.section === 'handoffs') await loadHandoffs()
  else await loadRuns()
}

export async function loadHandoffs() {
  state.handoffs = await staffApi.handoffs(state.token)
}

export async function openHandoff(handoff, { keepSelection = false } = {}) {
  state.selectedId = handoff.id
  state.conversationLoading = true
  try {
    state.conversation = await staffApi.conversation(state.token, handoff.conversation_id)
    if (!keepSelection) state.reply = ''
  } catch (cause) {
    state.conversation = null
    state.error = `会话详情加载失败：${cause.message}`
  } finally {
    state.conversationLoading = false
  }
}

export async function acceptSelected() {
  if (!state.selectedId) return
  clearFeedback()
  try {
    await staffApi.accept(state.token, state.selectedId)
    state.notice = '已接入会话'
    await loadHandoffs()
    const current = state.handoffs.find((item) => item.id === state.selectedId)
    if (current) await openHandoff(current, { keepSelection: true })
  } catch (cause) {
    if (cause.status === 409 || cause.status === 403) {
      await loadHandoffs()
      state.error = '该工单已被其他客服接入，队列已刷新。'
    } else {
      state.error = cause.message
    }
  }
}

export async function sendReply() {
  const text = state.reply.trim()
  if (!text || !state.selectedId) return
  clearFeedback()
  const messageId = newId('human')
  try {
    await staffApi.reply(state.token, state.selectedId, messageId, text)
    state.reply = ''
    if (state.conversation) {
      const handoff = selectedHandoff.value
      state.conversation = await staffApi.conversation(state.token, handoff.conversation_id)
    }
  } catch (cause) {
    state.error = cause.message
  }
}

export async function resolveSelected() {
  if (!state.selectedId) return
  if (!window.confirm('确认结束人工服务并交还 AI？结束后会进入重新核实状态。')) return
  clearFeedback()
  state.resolving = true
  try {
    await staffApi.resolve(state.token, state.selectedId)
    state.notice = '人工服务已结束，会话已切回 AI'
    state.selectedId = null
    state.conversation = null
    await loadHandoffs()
  } catch (cause) {
    state.error = cause.message
  } finally {
    state.resolving = false
  }
}

export async function loadRuns() {
  state.runsLoading = true
  try {
    state.runs = await aiApi.runs(state.token, state.runFilter)
    if (isAdmin.value) {
      state.metrics.ai = await aiApi.metrics(state.token).catch(() => ({}))
      state.metrics.service = await staffApi.serviceMetrics(state.token).catch(() => ({}))
      state.runtime = await aiApi.runtime(state.token).catch(() => null)
    }
  } finally {
    state.runsLoading = false
  }
}

export async function inspectRun(run) {
  clearFeedback()
  try {
    state.selectedRun = await aiApi.run(state.token, run.id)
  } catch (cause) {
    state.error = cause.message
  }
}

export function closeRun() {
  state.selectedRun = null
}

export function teardown() {
  window.clearTimeout(refreshTimer)
  realtime?.close()
}

export function messageText(message) {
  const content = message?.content || {}
  if (content.text) return content.text
  if (content.object_type === 'order') return `订单 ${content.object_id}`
  if (content.object_type === 'plan') return `套餐 ${content.object_id}`
  return '对象消息'
}

export function outcomeLabel(outcome) {
  return (
    {
      ACCEPTED: '已接纳',
      REJECTED: '已拒绝',
      UNKNOWN: '待确认',
    }[outcome] || outcome || '待确认'
  )
}
