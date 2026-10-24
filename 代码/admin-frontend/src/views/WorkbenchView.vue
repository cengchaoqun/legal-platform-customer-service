<script setup>
import { computed } from 'vue'
import {
  acceptSelected,
  canManage,
  messageText,
  openHandoff,
  resolveSelected,
  selectedHandoff,
  sendReply,
  state,
} from '../store'

const context = computed(() => state.conversation?.handoff_context || null)
const flow = computed(() => state.conversation?.flow_summary || null)
const queue = computed(() => state.handoffs)

const intentLabels = {
  PLAN_QUERY: '套餐咨询',
  ORDER_QUERY: '订单查询',
  INVOICE_QUERY: '开票查询',
  INVOICE_APPLY: '开票申请',
  SERVICE_PROGRESS_QUERY: '办理进度',
  REFUND_APPLY: '退款申请',
  AFTER_SALE_QUERY: '退款 / 售后进度',
  CANCEL_ORDER: '取消订单',
  POLICY_QUERY: '服务政策',
  COMPLAINT: '投诉',
  HUMAN_TRANSFER: '用户要求人工',
  LEGAL_ADVICE_REQUEST: '专业法律咨询',
  UNKNOWN: '意图待确认',
}

function intentLabel(value) {
  return intentLabels[value] || value || '未记录'
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

function verifiedEntries() {
  return Object.entries(context.value?.verified_slots || {})
}

function unverifiedEntries() {
  return Object.entries(context.value?.unverified_slots || {})
}
</script>

<template>
  <section>
    <header class="page-heading">
      <div>
        <p class="eyebrow">HUMAN SERVICE</p>
        <h1>人工工作台</h1>
        <p>接入后 AI 自动回复暂停；结束人工会进入安全恢复检查点，不会自动沿用旧资格。</p>
      </div>
      <span class="count-chip">{{ queue.length }} 个待处理 / 处理中</span>
    </header>

    <div class="workbench">
      <section class="queue-panel panel">
        <div class="panel-head">
          <h2>待接入 / 处理中</h2>
          <small>按创建时间</small>
        </div>
        <div class="queue-list">
          <button
            v-for="item in queue"
            :key="item.id"
            :class="['queue-item', { active: state.selectedId === item.id }]"
            @click="openHandoff(item)"
          >
            <span class="queue-body">
              <strong>{{ item.summary }}</strong>
              <small>{{ item.user_id }} · {{ intentLabel(item.context_preview?.intent) }}</small>
              <small v-if="item.context_preview?.order_id">订单 {{ item.context_preview.order_id }}</small>
            </span>
            <span class="queue-state">
              {{ item.status === 'active' ? `处理中 · ${item.assigned_agent_id}` : '待接入' }}
            </span>
          </button>
          <div v-if="!queue.length" class="empty-state compact">
            <strong>队列已清空</strong>
            <p>当前没有需要人工介入的会话。</p>
          </div>
        </div>
      </section>

      <section class="conversation-panel panel">
        <template v-if="selectedHandoff">
          <header class="conversation-head">
            <div>
              <strong>{{ selectedHandoff.user_id }}</strong>
              <small>{{ selectedHandoff.id }} · {{ selectedHandoff.conversation_id }}</small>
            </div>
            <div class="head-actions">
              <button v-if="selectedHandoff.status === 'waiting'" @click="acceptSelected">接入会话</button>
              <button
                v-else-if="canManage"
                class="ghost-button"
                :disabled="state.resolving"
                @click="resolveSelected"
              >
                {{ state.resolving ? '处理中…' : '结束人工服务' }}
              </button>
              <small v-else>由 {{ selectedHandoff.assigned_agent_id }} 处理中</small>
            </div>
          </header>

          <div class="transcript">
            <p v-if="state.conversationLoading" class="muted">正在加载会话…</p>
            <article
              v-for="message in state.conversation?.messages || []"
              :key="message.message_id"
              :class="['transcript-message', message.role]"
            >
              <span>{{ message.role === 'user' ? '用户' : message.role === 'human' ? '人工' : 'AI' }}</span>
              <div>
                <p>{{ messageText(message) }}</p>
                <time>{{ format(message.created_at) }}</time>
              </div>
            </article>
          </div>

          <form class="reply-box" @submit.prevent="sendReply">
            <textarea
              v-model="state.reply"
              rows="3"
              maxlength="4000"
              :disabled="!canManage"
              :placeholder="
                canManage
                  ? '回复用户；不要承诺已退款或已开具'
                  : selectedHandoff.status === 'waiting'
                    ? '接入后可以回复用户'
                    : `由 ${selectedHandoff.assigned_agent_id} 处理中`
              "
            />
            <div class="reply-actions">
              <small>{{ canManage ? '发送后同步到用户端与工单' : '仅负责人可回复' }}</small>
              <button :disabled="!canManage || !state.reply.trim()">发送回复</button>
            </div>
          </form>
        </template>
        <div v-else class="empty-state">
          <h2>选择一条待处理会话</h2>
          <p>左侧队列只显示你有权处理的工单。</p>
        </div>
      </section>

      <aside class="context-panel panel">
        <h2>交接上下文</h2>
        <template v-if="context">
          <section class="context-block">
            <h3>诉求与对象</h3>
            <dl>
              <dt>主要意图</dt>
              <dd>{{ intentLabel(context.primary_intent || context.intent) }}</dd>
              <dt>关联订单</dt>
              <dd>{{ context.order_id || '未确定' }}</dd>
              <dt>售后申请</dt>
              <dd>{{ context.after_sale_id || '—' }}</dd>
              <dt>待办意图</dt>
              <dd>{{ (context.pending_intents || []).map(intentLabel).join('、') || '无' }}</dd>
            </dl>
          </section>

          <section class="context-block">
            <h3>已确认信息</h3>
            <ul v-if="verifiedEntries().length" class="kv-list">
              <li v-for="[key, value] in verifiedEntries()" :key="key">
                <span>{{ key }}</span><strong>{{ value }}</strong>
              </li>
            </ul>
            <p v-else class="muted">暂无可信槽位。</p>
          </section>

          <section v-if="unverifiedEntries().length" class="context-block">
            <h3>待核实信息</h3>
            <ul class="kv-list">
              <li v-for="[key, value] in unverifiedEntries()" :key="key">
                <span>{{ key }}</span><strong>{{ value }}</strong>
              </li>
            </ul>
          </section>

          <section class="context-block">
            <h3>已尝试步骤</h3>
            <ul class="plain-list">
              <li v-for="step in context.attempted_steps || []" :key="step">{{ step }}</li>
              <li v-if="!context.attempted_steps?.length">未记录</li>
            </ul>
          </section>

          <section v-if="context.conflicts?.length" class="context-block">
            <h3>冲突</h3>
            <p v-for="item in context.conflicts" :key="item.slot_name" class="error-text">
              {{ item.slot_name }}：{{ item.existing_value }} → {{ item.new_value }}（{{ item.reason }}）
            </p>
          </section>

          <section class="context-block">
            <h3>流程检查点</h3>
            <dl>
              <dt>流程</dt>
              <dd>{{ intentLabel(flow?.flow_type) }}</dd>
              <dt>节点</dt>
              <dd>{{ flow?.node || context.flow_node || '—' }}</dd>
              <dt>状态版本</dt>
              <dd>{{ flow?.state_version ?? context.flow_state_version ?? '—' }}</dd>
            </dl>
          </section>
        </template>
        <p v-else class="muted">该会话暂无结构化交接上下文。</p>
        <p class="rail-note">上下文按角色与工单范围展示；敏感资料不在运行记录中展示。</p>
      </aside>
    </div>
  </section>
</template>
