<script setup>
import { onMounted, ref } from 'vue'
import { historyGroups, loadHistory, state, textOf } from '../store'

const expanded = ref(new Set())

onMounted(() => {
  if (!state.history.length) loadHistory().catch((cause) => (state.error = cause.message))
  const current = historyGroups.value.find((group) => group.id === state.conversationId)
  if (current) expanded.value = new Set([current.id])
})

function toggle(id) {
  const next = new Set(expanded.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expanded.value = next
}

function format(value) {
  if (!value) return ''
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

function roleLabel(message) {
  if (message.role === 'user') return '我'
  if (message.role === 'human') return '人工客服'
  return 'AI 客服'
}
</script>

<template>
  <section>
    <header class="page-heading">
      <div>
        <p class="eyebrow">CONVERSATION ARCHIVE</p>
        <h1>历史咨询</h1>
        <p>按会话开始时间整理，展开可以回看当时的完整记录。</p>
      </div>
      <span class="count-chip">{{ historyGroups.length }} 次会话</span>
    </header>

    <div v-if="!historyGroups.length" class="empty-state">
      <h2>还没有历史会话</h2>
      <p>完成第一次咨询后，记录会显示在这里。</p>
    </div>

    <div v-else class="history-list">
      <article v-for="(group, index) in historyGroups" :key="group.id" class="history-item">
        <button class="history-head" :aria-expanded="expanded.has(group.id)" @click="toggle(group.id)">
          <span class="history-index">{{ String(index + 1).padStart(2, '0') }}</span>
          <span class="history-summary">
            <strong>{{ format(group.startedAt) }}</strong>
            <small>{{ group.preview }}</small>
          </span>
          <span class="history-count">{{ group.messages.length }} 条 · {{ expanded.has(group.id) ? '收起' : '展开' }}</span>
        </button>
        <div v-if="expanded.has(group.id)" class="history-body">
          <article v-for="message in group.messages" :key="message.message_id || message.sequence" :class="['history-message', message.role]">
            <span>{{ roleLabel(message) }}</span>
            <div>
              <p>{{ textOf(message) }}</p>
              <time>{{ format(message.created_at) }}</time>
            </div>
          </article>
        </div>
      </article>
    </div>
  </section>
</template>
