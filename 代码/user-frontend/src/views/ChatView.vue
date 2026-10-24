<script setup>
import { nextTick, onMounted, ref, watch } from 'vue'
import {
  navigate,
  requestHuman,
  retryLastMessage,
  sendObject,
  sendText,
  setScrollTarget,
  state,
  textOf,
} from '../store'

const draft = ref('')
const listRef = ref(null)
const sendingChoice = ref(false)

const suggestions = [
  '合同审核套餐我想退款',
  '我的订单发票开好了吗',
  '合同审核进行到哪一步了',
]

onMounted(() => setScrollTarget(listRef.value))
watch(listRef, (element) => setScrollTarget(element))

async function submit() {
  const value = draft.value.trim()
  if (!value) return
  draft.value = ''
  await sendText(value)
}

async function choose(choice) {
  if (sendingChoice.value) return
  sendingChoice.value = true
  try {
    await sendObject(choice.object_type, choice.object_id)
  } finally {
    sendingChoice.value = false
  }
}

function openAction(href) {
  if (href) navigate(href)
}

function retry() {
  retryLastMessage()
}

function talkToHuman() {
  requestHuman()
}

function formatTime(value) {
  if (!value) return ''
  return new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(
    new Date(value),
  )
}

function roleLabel(message) {
  if (message.role === 'user') return '我'
  if (message.role === 'human') return '人工'
  return 'AI'
}

function refresh() {
  // 供模板在消息追加后滚动
  nextTick(() => listRef.value?.scrollTo({ top: listRef.value.scrollHeight, behavior: 'smooth' }))
}
</script>

<template>
  <section class="chat-layout">
    <div class="chat-panel">
      <div class="chat-heading">
        <div>
          <p class="eyebrow">AI CUSTOMER SERVICE</p>
          <h1>有问题，直接问</h1>
          <p>套餐、订单、开票、办理进度与退款售后都可以在这里咨询；业务办理会在页面确认后提交。</p>
        </div>
        <span :class="['mode-chip', state.mode.toLowerCase()]">
          {{ state.mode === 'HUMAN' ? '人工服务中' : state.mode === 'QUEUED' ? '等待人工' : '智能客服' }}
        </span>
      </div>

      <div ref="listRef" class="message-list" aria-live="polite">
        <div v-if="!state.messages.length" class="chat-empty">
          <strong>可以先从这些问题开始</strong>
          <button v-for="item in suggestions" :key="item" @click="draft = item; submit()">
            {{ item }}
          </button>
        </div>

        <article
          v-for="message in state.messages"
          :key="message.message_id || message.sequence"
          :class="['message', message.role]"
        >
          <span class="avatar">{{ roleLabel(message) }}</span>
          <div class="bubble">
            <p class="bubble-text">{{ textOf(message) }}</p>

            <div v-if="message.content?.choices?.length" class="choice-grid">
              <button
                v-for="choice in message.content.choices"
                :key="`${choice.object_type}:${choice.object_id}`"
                :disabled="sendingChoice"
                @click="choose(choice)"
              >
                <span>
                  <strong>{{ choice.label }}</strong>
                  <small>{{ choice.description }}</small>
                </span>
                <b>选择</b>
              </button>
            </div>

            <button
              v-if="message.content?.action?.href"
              class="action-card"
              @click="openAction(message.content.action.href)"
            >
              <span>
                <strong>{{ message.content.action.label }}</strong>
                <small>{{ message.content.action.description }}</small>
              </span>
              <b>前往</b>
            </button>

            <small class="message-time">
              {{ formatTime(message.created_at) }}
              <template v-if="message.delivery_status === 'sending'"> · 发送中</template>
              <template v-else-if="message.delivery_status === 'failed'"> · 发送失败</template>
            </small>
          </div>
        </article>

        <article v-if="state.processing" class="message ai" role="status">
          <span class="avatar">AI</span>
          <div class="bubble processing">
            <span class="dots" aria-hidden="true"><i></i><i></i><i></i></span>
            正在查询并核对业务信息…
          </div>
        </article>
      </div>

      <div v-if="state.lastFailedMessage" class="retry-bar">
        上一条消息发送失败
        <button @click="retry">重试（沿用原消息编号）</button>
      </div>

      <form class="composer" @submit.prevent="submit">
        <textarea
          v-model="draft"
          rows="3"
          maxlength="2000"
          placeholder="描述你的问题；请勿在会话中发送完整合同或证件资料"
          @keydown.enter.exact.prevent="submit"
        />
        <div class="composer-actions">
          <small>
            {{ state.mode === 'AI' ? '业务操作需在页面确认，客服不会代替你提交' : '人工会话中，AI 自动回复已暂停' }}
          </small>
          <button :disabled="!draft.trim()">发送</button>
        </div>
      </form>
    </div>

    <aside class="context-rail">
      <h2>关联信息</h2>
      <section class="rail-block">
        <h3>本次会话</h3>
        <dl>
          <dt>服务模式</dt>
          <dd>{{ state.mode === 'HUMAN' ? '人工客服' : state.mode === 'QUEUED' ? '等待人工接入' : '智能客服' }}</dd>
          <dt>消息状态</dt>
          <dd>{{ state.processing ? '正在处理' : '空闲' }}</dd>
          <dt>实时连接</dt>
          <dd>{{ state.wsState === 'connected' ? '已连接' : '连接中' }}</dd>
        </dl>
      </section>

      <section class="rail-block">
        <h3>需要自己查看</h3>
        <button class="ghost-button" @click="navigate('/me')">查看我的订单</button>
        <button class="ghost-button" @click="navigate('/plans')">查看服务套餐</button>
      </section>

      <section class="rail-block">
        <h3>人工服务</h3>
        <p>需要人工核实、投诉或涉及具体案情时，可以转人工。转接期间 AI 自动回复会暂停。</p>
        <button class="ghost-button" @click="talkToHuman">转人工客服</button>
      </section>

      <p class="rail-note">
        页面只展示必要的业务状态，不展示模型决策过程。具体法律意见请通过专业咨询渠道确认。
      </p>
    </aside>
  </section>
</template>
