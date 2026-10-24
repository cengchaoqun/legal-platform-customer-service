<script setup>
import { computed, onBeforeUnmount, onMounted } from 'vue'
import WorkbenchView from './views/WorkbenchView.vue'
import RunsView from './views/RunsView.vue'
import { authenticate, isAdmin, state, switchSection, teardown } from './store'

const title = computed(() => (state.section === 'runs' ? 'AI 运行记录' : '人工工作台'))
const view = computed(() => (state.section === 'runs' ? RunsView : WorkbenchView))

onMounted(authenticate)
onBeforeUnmount(teardown)
</script>

<template>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand">
        <span class="brand-mark">掌</span>
        <span><strong>掌柜 · 客服工作台</strong><small>人工服务与运行审计</small></span>
      </div>

      <div class="presence">
        <span :class="['dot', state.wsState]"></span>
        {{ state.wsState === 'connected' ? '在线 · 可接入' : '正在连接' }}
      </div>

      <nav class="side-nav" aria-label="后台导航">
        <button :class="{ active: state.section === 'handoffs' }" @click="switchSection('handoffs')">
          人工工作台
        </button>
        <button
          v-if="isAdmin"
          :class="{ active: state.section === 'runs' }"
          @click="switchSection('runs')"
        >
          AI 运行记录
        </button>
      </nav>

      <div class="side-section">
        <p>当前队列</p>
        <span class="queue-count">{{ state.handoffs.length }}</span>
        <small>仅展示等待接入与本人处理的工单</small>
      </div>

      <div class="side-section">
        <p>角色</p>
        <span class="role-chip">{{ state.principal?.role === 'admin' ? '管理员' : '人工客服' }}</span>
        <small v-if="!isAdmin">普通客服不可查看完整 AI 审计</small>
        <small v-else>可查看运行审计与全部会话</small>
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
          <small>{{ state.principal?.display_name || '加载中' }}</small>
        </div>
        <div class="top-actions">
          <span :class="['connection', state.wsState]">
            {{ state.wsState === 'connected' ? '实时在线' : '连接中' }}
          </span>
          <label class="account">
            <span>员工账号</span>
            <select v-model="state.account" @change="authenticate">
              <option value="agent_001">客服小林（agent_001）</option>
              <option value="agent_002">客服小周（agent_002）</option>
              <option value="admin_001">系统管理员（admin_001）</option>
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
