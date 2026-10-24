<script setup>
import { onMounted } from 'vue'
import { consultOrder, loadOrders, navigate, state } from '../store'

onMounted(() => {
  if (!state.orders.length) loadOrders().catch((cause) => (state.error = cause.message))
})

function formatDate(value) {
  if (!value) return ''
  return new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' }).format(
    new Date(value),
  )
}

function statusClass(status) {
  if (status === 'CANCELLED') return 'cancelled'
  if (status === 'COMPLETED' || status === 'DELIVERED') return 'done'
  if (status === 'WAITING_MATERIALS') return 'attention'
  return 'active'
}
</script>

<template>
  <section>
    <header class="page-heading">
      <div>
        <p class="eyebrow">MY ORDERS</p>
        <h1>我的订单</h1>
        <p>订单只展示当前账号下的服务记录；状态与可用操作由业务系统返回。</p>
      </div>
      <span class="count-chip">{{ state.orders.length }} 笔</span>
    </header>

    <div v-if="!state.orders.length" class="empty-state">
      <h2>暂无订单</h2>
      <p>当前账号还没有法律服务订单。可以先看看服务套餐。</p>
      <button class="ghost-button" @click="navigate('/plans')">查看服务套餐</button>
    </div>

    <div v-else class="order-list">
      <article v-for="order in state.orders" :key="order.id" class="order-row">
        <div class="order-main">
          <div class="order-title">
            <strong>{{ order.service_name }}</strong>
            <span :class="['status-chip', statusClass(order.service_status)]">
              {{ order.service_status_label || order.service_status }}
            </span>
          </div>
          <p class="muted">
            订单 {{ order.id }} · 下单 {{ formatDate(order.created_at) }} · 实付 ¥{{ order.total_amount }}
          </p>
        </div>
        <div class="order-actions">
          <button class="ghost-button" @click="navigate(`/me/orders/${order.id}`)">查看详情</button>
          <button @click="consultOrder(order.id)">咨询该订单</button>
        </div>
      </article>
    </div>
  </section>
</template>
