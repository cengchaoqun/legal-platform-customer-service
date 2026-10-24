<script setup>
import { computed, onBeforeUnmount, onMounted } from 'vue'
import ChatView from './views/ChatView.vue'
import PlansView from './views/PlansView.vue'
import OrdersView from './views/OrdersView.vue'
import OrderDetailView from './views/OrderDetailView.vue'
import RefundView from './views/RefundView.vue'
import InvoiceView from './views/InvoiceView.vue'
import HistoryView from './views/HistoryView.vue'
import {
  bootstrap,
  loadRoute,
  modeLabel,
  navigate,
  operation,
  path,
  state,
  switchUser,
  teardown,
} from './store'

const title = computed(() => {
  if (path.value === '/plans') return '服务套餐'
  if (path.value === '/me') return '我的订单'
  if (path.value === '/history') return '历史咨询'
  if (path.value === '/chat' || path.value === '/') return '智能客服'
  if (operation.value === 'refund') return '退款 / 售后申请'
  if (operation.value === 'invoice') return '开票申请'
  return '订单详情'
})

const view = computed(() => {
  if (path.value === '/plans') return PlansView
  if (path.value === '/me') return OrdersView
  if (path.value === '/history') return HistoryView
  if (operation.value === 'refund') return RefundView
  if (operation.value === 'invoice') return InvoiceView
  if (path.value.startsWith('/me/orders/')) return OrderDetailView
  return ChatView
})

const navItems = [
  { label: '智能客服', to: '/chat', match: ['/chat', '/'] },
  { label: '服务套餐', to: '/plans', match: ['/plans'] },
  { label: '我的订单', to: '/me', match: ['/me'] },
  { label: '历史咨询', to: '/history', match: ['/history'] },
]

const customers = computed(() => state.users.filter((user) => user.role === 'customer'))

function onPopState() {
  state.route = `${location.pathname}${location.search}`
  loadRoute()
}

onMounted(() => {
  window.addEventListener('popstate', onPopState)
  bootstrap()
})
onBeforeUnmount(() => {
  window.removeEventListener('popstate', onPopState)
  teardown()
})
</script>

<template>
  <div class="shell">
    <aside class="sidebar">
      <button class="brand" @click="navigate('/chat')">
        <span class="brand-mark">掌</span>
        <span><strong>掌柜 · 法律客服</strong><small>平台用户咨询与售后</small></span>
      </button>

      <button class="primary-action" @click="navigate('/chat')">＋ 新建咨询</button>

      <nav class="side-nav" aria-label="主导航">
        <button
          v-for="item in navItems"
          :key="item.to"
          :class="{ active: item.match.some((entry) => path === entry || path.startsWith(`${entry}/`)) }"
          @click="navigate(item.to)"
        >
          {{ item.label }}
        </button>
      </nav>

      <div class="side-section">
        <p>当前会话</p>
        <span :class="['mode-chip', state.mode.toLowerCase()]">{{ modeLabel }}</span>
        <small v-if="state.handoffStatus === 'waiting'">已提交人工请求，请稍候</small>
        <small v-else-if="state.handoffStatus === 'active'">人工客服正在处理</small>
        <small v-else>{{ state.wsState === 'connected' ? '实时连接正常' : '正在连接实时通道' }}</small>
      </div>

      <footer class="side-footer">
        <small>演示工作空间</small>
        <small>业务数据为演示数据</small>
      </footer>
    </aside>

    <div class="main-area">
      <header class="topbar">
        <div>
          <strong>{{ title }}</strong>
          <small>用户工作台</small>
        </div>
        <div class="top-actions">
          <span :class="['connection', state.wsState]">
            {{ state.wsState === 'connected' ? '实时在线' : '连接中' }}
          </span>
          <label class="account">
            <span>体验账号</span>
            <select :value="state.selectedUser" @change="switchUser($event.target.value)">
              <option v-for="user in customers" :key="user.id" :value="user.id">
                {{ user.name }}（{{ user.id }}）
              </option>
            </select>
          </label>
        </div>
      </header>

      <main class="content">
        <div v-if="state.error" class="alert error" role="alert">
          {{ state.error }}
          <button aria-label="关闭提示" @click="state.error = ''">×</button>
        </div>
        <div v-if="state.notice" class="alert success" role="status">
          {{ state.notice }}
          <button aria-label="关闭提示" @click="state.notice = ''">×</button>
        </div>
        <component :is="view" />
      </main>
    </div>
  </div>
</template>
