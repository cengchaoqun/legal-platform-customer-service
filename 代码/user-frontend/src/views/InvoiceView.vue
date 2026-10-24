<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import {
  loadOrderDetail,
  navigate,
  orderId,
  resetWriteState,
  state,
  submitInvoice,
  verifyWriteResult,
} from '../store'

const form = reactive({
  invoice_type: 'ELECTRONIC',
  title_type: 'PERSONAL',
  title: '',
  tax_id: '',
  email: '',
})
const errors = reactive({})

const order = computed(() => state.order)
const invoice = computed(() => state.invoice)
const canApply = computed(() => (order.value?.allowed_actions || []).includes('APPLY_INVOICE'))
const blocked = computed(() =>
  (order.value?.allowed_actions || []).includes('APPLY_INVOICE') ? '' : '当前订单状态不允许申请开票。',
)
const existingStatus = computed(() => invoice.value?.status || 'NONE')

onMounted(async () => {
  if (!state.order || state.order.id !== orderId.value) {
    await loadOrderDetail(orderId.value).catch((cause) => (state.error = cause.message))
  }
  form.title = state.order?.payment_status_label ? '' : ''
  resetWriteState()
})

function validate() {
  Object.keys(errors).forEach((key) => delete errors[key])
  if (!form.title.trim()) errors.title = '请填写发票抬头。'
  if (form.title_type === 'COMPANY' && !form.tax_id.trim()) errors.tax_id = '企业抬头必须填写税号。'
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email)) errors.email = '请填写可接收发票的邮箱。'
  return Object.keys(errors).length === 0
}

async function submit() {
  if (!validate()) return
  await submitInvoice({ ...form })
}
</script>

<template>
  <section v-if="order">
    <header class="page-heading">
      <div>
        <button class="back" @click="navigate(`/me/orders/${order.id}`)">← 返回订单</button>
        <h1>开票申请</h1>
        <p class="muted">{{ order.service_name }} · 订单 {{ order.id }} · 可开票金额 ¥{{ order.total_amount }}</p>
      </div>
      <span class="status-chip">{{ invoice?.status_label || '尚未申请开票' }}</span>
    </header>

    <div class="two-column">
      <section class="panel">
        <template v-if="state.writeState === 'success'">
          <h2>开票申请已受理</h2>
          <dl class="detail-list">
            <dt>申请编号</dt>
            <dd>{{ state.writeResult?.invoice_id }}</dd>
            <dt>状态</dt>
            <dd>{{ state.writeResult?.status_label }}</dd>
            <dt>金额</dt>
            <dd>¥{{ order.total_amount }}</dd>
          </dl>
          <p class="notice-block">
            受理不等于已开具。发票开具完成后，订单详情的开票状态会更新。
          </p>
          <div class="button-row">
            <button class="ghost-button" @click="navigate(`/me/orders/${order.id}`)">查看订单</button>
            <button @click="navigate('/chat')">继续咨询</button>
          </div>
        </template>

        <template v-else-if="existingStatus === 'ISSUED'">
          <h2>发票已开具</h2>
          <p>该订单的发票已经开具，可在订单详情查看状态。如需重新开具，请联系人工客服核实。</p>
          <button class="ghost-button" @click="navigate(`/me/orders/${order.id}`)">返回订单</button>
        </template>

        <template v-else>
          <h2>填写开票资料</h2>
          <p class="muted">完整税号与邮箱只提交给业务系统，不进入客服会话或模型输入。</p>

          <div v-if="blocked" class="alert warning">{{ blocked }}</div>
          <div v-if="state.writeState === 'unknown'" class="alert warning">
            {{ state.writeError }}
            <button class="inline-button" @click="verifyWriteResult">核对结果</button>
          </div>
          <div v-if="state.writeState === 'failed'" class="alert error">{{ state.writeError }}</div>

          <form class="form" @submit.prevent="submit">
            <label>
              <span>发票类型</span>
              <select v-model="form.invoice_type" :disabled="!canApply">
                <option value="ELECTRONIC">电子发票</option>
                <option value="PAPER">纸质发票</option>
              </select>
            </label>
            <label>
              <span>抬头类型</span>
              <select v-model="form.title_type" :disabled="!canApply">
                <option value="PERSONAL">个人</option>
                <option value="COMPANY">企业</option>
              </select>
            </label>
            <label>
              <span>发票抬头 <b>*</b></span>
              <input v-model="form.title" maxlength="120" :disabled="!canApply" />
              <small v-if="errors.title" class="field-error">{{ errors.title }}</small>
            </label>
            <label v-if="form.title_type === 'COMPANY'">
              <span>纳税人识别号 <b>*</b></span>
              <input v-model="form.tax_id" maxlength="40" :disabled="!canApply" />
              <small v-if="errors.tax_id" class="field-error">{{ errors.tax_id }}</small>
            </label>
            <label>
              <span>接收邮箱 <b>*</b></span>
              <input v-model="form.email" type="email" maxlength="160" :disabled="!canApply" />
              <small v-if="errors.email" class="field-error">{{ errors.email }}</small>
            </label>
            <button :disabled="!canApply || state.writeState === 'submitting'">
              {{ state.writeState === 'submitting' ? '提交中…' : '确认提交开票申请' }}
            </button>
          </form>
        </template>
      </section>

      <aside class="panel side-note">
        <h2>当前开票状态</h2>
        <dl class="detail-list">
          <dt>状态</dt>
          <dd>{{ invoice?.status_label || '尚未申请开票' }}</dd>
          <dt>金额</dt>
          <dd>¥{{ invoice?.amount || order.total_amount }}</dd>
          <dt>更新时间</dt>
          <dd>{{ invoice?.updated_at ? new Date(invoice.updated_at).toLocaleString() : '—' }}</dd>
        </dl>
        <p v-if="invoice?.failure_reason" class="error-text">失败原因：{{ invoice.failure_reason }}</p>
        <p class="rail-note">客服不能承诺固定开具时间；申请受理后请以订单详情状态为准。</p>
      </aside>
    </div>
  </section>
  <div v-else class="empty-state"><p>正在加载订单…</p></div>
</template>
