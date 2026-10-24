<script setup>
import { computed } from 'vue'
import { navigate, query, state, submitCancel } from '../store'

const order = computed(() => state.order)
const progress = computed(() => state.progress)
const invoice = computed(() => state.invoice)
const afterSales = computed(() => state.afterSales)
const cancelMode = computed(() => query.value.get('action') === 'cancel')

function can(action) {
  return (order.value?.allowed_actions || []).includes(action)
}

function formatDate(value) {
  if (!value) return ''
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

function invoiceStatusLabel(status) {
  return {
    NONE: '尚未申请开票',
    SUBMITTED: '开票申请已受理',
    PROCESSING: '开票处理中',
    ISSUED: '发票已开具',
    FAILED: '开票失败',
  }[status] || status
}
</script>

<template>
  <section v-if="order">
    <header class="page-heading">
      <div>
        <button class="back" @click="navigate('/me')">← 返回订单</button>
        <h1>{{ order.service_name }}</h1>
        <p class="muted">
          订单 {{ order.id }} · 下单 {{ formatDate(order.created_at) }} · 实付 ¥{{ order.total_amount }}
        </p>
      </div>
      <span class="status-chip active">{{ order.service_status_label }}</span>
    </header>

    <div class="detail-layout">
      <div class="detail-main">
        <section class="panel">
          <div class="panel-head">
            <h2>办理进度</h2>
            <small>更新时间 {{ formatDate(progress?.updated_at) }}</small>
          </div>
          <div v-if="progress" class="timeline">
            <article
              v-for="(event, index) in progress.events"
              :key="`${event.node}-${index}`"
              :class="['timeline-item', { current: index === progress.events.length - 1 }]"
            >
              <span class="timeline-index">{{ String(index + 1).padStart(2, '0') }}</span>
              <div>
                <strong>{{ event.node }}</strong>
                <p>{{ event.description }}</p>
                <small>{{ formatDate(event.time) }}</small>
              </div>
            </article>
          </div>
          <p v-else class="muted">暂时没有服务记录。</p>

          <div v-if="progress?.missing_materials?.length" class="notice-block">
            <strong>还需要补充材料</strong>
            <ul>
              <li v-for="item in progress.missing_materials" :key="item">{{ item }}</li>
            </ul>
            <p>材料需在服务页面提交，请勿直接发在客服会话中。</p>
          </div>
          <p v-if="progress && !progress.estimated_at" class="muted">
            没有业务方提供的预计完成时间，以服务记录更新为准。
          </p>
        </section>

        <section v-if="cancelMode" class="panel">
          <h2>确认取消订单</h2>
          <p>取消前已重新读取订单状态。取消成功后会立即生效。</p>
          <div v-if="state.writeState === 'failed'" class="alert error">{{ state.writeError }}</div>
          <div v-if="state.writeState === 'unknown'" class="alert warning">
            {{ state.writeError }}
            <button @click="navigate(`/me/orders/${order.id}`)">刷新状态</button>
          </div>
          <button
            class="danger"
            :disabled="!can('CANCEL_ORDER') || state.writeState === 'submitting'"
            @click="submitCancel"
          >
            {{ state.writeState === 'submitting' ? '提交中…' : '确认取消订单' }}
          </button>
          <p v-if="!can('CANCEL_ORDER')" class="muted">订单当前状态不能直接取消，可申请售后。</p>
        </section>

        <section class="panel">
          <div class="panel-head"><h2>退款 / 售后记录</h2></div>
          <div v-if="afterSales.length" class="record-list">
            <article v-for="record in afterSales" :key="record.id" class="record">
              <div>
                <strong>{{ record.kind_label }}</strong>
                <p>{{ record.reason }}</p>
                <small>{{ record.id }} · 更新 {{ formatDate(record.updated_at) }}</small>
              </div>
              <div class="record-status">
                <span class="status-chip">{{ record.application_status_label }}</span>
                <span class="status-chip subtle">{{ record.refund_status_label }}</span>
              </div>
            </article>
          </div>
          <p v-else class="muted">该订单暂无售后申请。</p>
          <button v-if="can('APPLY_AFTER_SALE')" class="ghost-button" @click="navigate(`/me/orders/${order.id}/refund`)">
            申请退款 / 售后
          </button>
        </section>
      </div>

      <aside class="detail-side">
        <section class="panel">
          <h2>订单与权益</h2>
          <dl class="detail-list">
            <dt>服务</dt>
            <dd>{{ order.service_name }}</dd>
            <dt>支付状态</dt>
            <dd>{{ order.payment_status_label }}</dd>
            <dt>订单金额</dt>
            <dd>¥{{ order.total_amount }}</dd>
            <dt>开票状态</dt>
            <dd>{{ invoiceStatusLabel(invoice?.status || order.invoice_status) }}</dd>
          </dl>
        </section>

        <section class="panel">
          <h2>发票</h2>
          <p class="muted">{{ invoice?.status_label || invoiceStatusLabel(invoice?.status) }}</p>
          <button
            v-if="can('APPLY_INVOICE')"
            class="ghost-button"
            @click="navigate(`/me/orders/${order.id}/invoice`)"
          >
            申请开票
          </button>
          <small v-else>当前状态不可申请开票。</small>
        </section>

        <section class="panel">
          <h2>操作</h2>
          <div class="stack">
            <button v-if="can('CANCEL_ORDER')" class="ghost-button danger-text" @click="navigate(`/me/orders/${order.id}?action=cancel`)">
              取消订单
            </button>
            <button
              v-if="can('APPLY_AFTER_SALE')"
              class="ghost-button"
              @click="navigate(`/me/orders/${order.id}/refund`)"
            >
              申请退款 / 售后
            </button>
            <button class="ghost-button" @click="navigate(`/me/orders/${order.id}?section=after-sales`)">
              查看售后记录
            </button>
          </div>
        </section>

        <p class="rail-note">页面按钮是否可用由业务系统返回的 allowed_actions 决定，提交时服务端会再次校验。</p>
      </aside>
    </div>
  </section>
  <div v-else class="empty-state"><p>正在加载订单…</p></div>
</template>
