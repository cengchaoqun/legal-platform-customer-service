<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  loadOrderDetail,
  navigate,
  orderId,
  resetWriteState,
  state,
  submitRefund,
  verifyWriteResult,
} from '../store'

const reason = ref('')
const amount = ref('')
const fieldError = ref('')

const order = computed(() => state.order)
const canApply = computed(() => (order.value?.allowed_actions || []).includes('APPLY_AFTER_SALE'))
const blocked = computed(() =>
  (order.value?.allowed_actions || []).includes('APPLY_AFTER_SALE') ? '' : '当前订单状态或已有申请导致不能再次提交退款申请。',
)

onMounted(async () => {
  if (!state.order || state.order.id !== orderId.value) {
    await loadOrderDetail(orderId.value).catch((cause) => (state.error = cause.message))
  }
  resetWriteState()
})

async function submit() {
  fieldError.value = ''
  if (reason.value.trim().length < 2) {
    fieldError.value = '请填写至少 2 个字的退款原因。'
    return
  }
  if (amount.value && !/^\d+(\.\d{1,2})?$/.test(amount.value)) {
    fieldError.value = '申请金额只能是数字，最多两位小数。'
    return
  }
  await submitRefund({ reason: reason.value.trim(), amount: amount.value || null })
}
</script>

<template>
  <section v-if="order">
    <header class="page-heading">
      <div>
        <button class="back" @click="navigate(`/me/orders/${order.id}`)">← 返回订单</button>
        <h1>退款 / 售后申请</h1>
        <p class="muted">{{ order.service_name }} · 订单 {{ order.id }} · 实付 ¥{{ order.total_amount }}</p>
      </div>
      <span class="status-chip">{{ order.service_status_label }}</span>
    </header>

    <div class="two-column">
      <section class="panel">
        <template v-if="state.writeState === 'success'">
          <h2>申请已提交</h2>
          <dl class="detail-list">
            <dt>申请编号</dt>
            <dd>{{ state.writeResult?.after_sale_id }}</dd>
            <dt>申请状态</dt>
            <dd>{{ state.writeResult?.application_status_label }}</dd>
            <dt>退款状态</dt>
            <dd>{{ state.writeResult?.refund_status_label }}</dd>
          </dl>
          <p class="notice-block">
            申请已提交，需要人工审核。审核通过与退款到账是两个不同状态，请以后续进度为准。
          </p>
          <div class="button-row">
            <button class="ghost-button" @click="navigate(`/me/orders/${order.id}`)">查看订单</button>
            <button @click="navigate('/chat')">继续咨询</button>
          </div>
        </template>

        <template v-else>
          <h2>填写申请信息</h2>
          <p class="muted">提交前会再次校验订单状态与申请资格。</p>

          <div v-if="blocked" class="alert warning">{{ blocked }}</div>
          <div v-if="state.writeState === 'unknown'" class="alert warning">
            {{ state.writeError }}
            <button class="inline-button" @click="verifyWriteResult">核对结果</button>
          </div>
          <div v-if="state.writeState === 'failed'" class="alert error">{{ state.writeError }}</div>
          <div v-if="fieldError" class="alert error">{{ fieldError }}</div>

          <form class="form" @submit.prevent="submit">
            <label>
              <span>退款原因 <b>*</b></span>
              <textarea
                v-model="reason"
                rows="5"
                maxlength="500"
                placeholder="例如：买错套餐，服务尚未开始"
                :disabled="!canApply || state.writeState === 'submitting'"
              />
            </label>
            <label>
              <span>申请金额（可选）</span>
              <input
                v-model="amount"
                inputmode="decimal"
                placeholder="不填则按订单金额审核"
                :disabled="!canApply || state.writeState === 'submitting'"
              />
              <small>金额由业务系统按资格判定，客服不能承诺可退金额。</small>
            </label>
            <button :disabled="!canApply || state.writeState === 'submitting'">
              {{ state.writeState === 'submitting' ? '提交中…' : '确认提交申请' }}
            </button>
          </form>
        </template>
      </section>

      <aside class="panel side-note">
        <h2>提交前请确认</h2>
        <ul class="plain-list">
          <li>退款申请提交后进入人工审核，不等于审核通过。</li>
          <li>审核通过后进入退款处理，到账时间受支付渠道影响。</li>
          <li>申请资格以业务系统返回的订单状态为准。</li>
          <li>网络中断时不要重复提交，先核对原申请结果。</li>
        </ul>
      </aside>
    </div>
  </section>
  <div v-else class="empty-state"><p>正在加载订单…</p></div>
</template>
