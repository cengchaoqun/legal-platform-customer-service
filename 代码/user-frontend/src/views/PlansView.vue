<script setup>
import { ref } from 'vue'
import { legalApi } from '../api'
import { consultPlan, searchPlans, state } from '../store'

const detail = ref(null)
const detailLoading = ref(false)
const detailError = ref('')

const serviceTypes = [
  { value: '', label: '全部服务' },
  { value: 'CONTRACT_REVIEW', label: '合同审核' },
  { value: 'LEGAL_CONSULT', label: '法律咨询' },
  { value: 'DOC_DRAFT', label: '文书起草' },
  { value: 'ANNUAL_ADVISOR', label: '企业顾问' },
]

async function openDetail(planId) {
  detailLoading.value = true
  detailError.value = ''
  try {
    // 详情接口返回服务范围等完整字段；列表只返回摘要
    detail.value = await legalApi.plan(state.token, planId)
  } catch (cause) {
    detailError.value = cause.message
  } finally {
    detailLoading.value = false
  }
}
</script>

<template>
  <section>
    <header class="page-heading">
      <div>
        <p class="eyebrow">SERVICE PLANS</p>
        <h1>法律服务套餐</h1>
        <p>价格、权益与有效期以套餐详情为准；套餐不包含诉讼代理或结果承诺。</p>
      </div>
      <span class="count-chip">{{ state.plans.length }} 个套餐</span>
    </header>

    <div class="filter-bar">
      <input
        v-model="state.planQuery"
        placeholder="搜索服务名称或范围，例如“合同审核”"
        @keydown.enter="searchPlans"
      />
      <select v-model="state.planType" @change="searchPlans">
        <option v-for="item in serviceTypes" :key="item.value" :value="item.value">
          {{ item.label }}
        </option>
      </select>
      <button @click="searchPlans">查询</button>
    </div>

    <div v-if="state.plansLoading" class="empty-state"><p>正在加载套餐…</p></div>

    <div v-else-if="!state.plans.length" class="empty-state">
      <h2>没有匹配的套餐</h2>
      <p>可以调整搜索词，或直接咨询客服确认服务范围。</p>
    </div>

    <div v-else class="plan-grid">
      <article v-for="plan in state.plans" :key="plan.id" class="plan-card">
        <div class="plan-card-head">
          <span class="tag">{{ plan.service_type }}</span>
          <span class="plan-version">版本 {{ plan.version }}</span>
        </div>
        <h2>{{ plan.name }}</h2>
        <p class="plan-price"><strong>¥{{ plan.price }}</strong> / 次</p>
        <ul class="rights">
          <li v-for="right in plan.rights" :key="right">{{ right }}</li>
        </ul>
        <p class="muted">{{ plan.validity_description }}</p>
        <div class="plan-actions">
          <button class="ghost-button" @click="openDetail(plan.id)">查看权益</button>
          <button @click="consultPlan(plan.id)">咨询该套餐</button>
        </div>
      </article>
    </div>

    <div v-if="detail || detailLoading || detailError" class="drawer-mask" @click.self="detail = null">
      <aside class="drawer">
        <header>
          <h2>{{ detail?.name || '套餐权益' }}</h2>
          <button class="icon-button" aria-label="关闭" @click="detail = null">×</button>
        </header>
        <p v-if="detailLoading">正在读取套餐详情…</p>
        <p v-if="detailError" class="error-text">{{ detailError }}</p>
        <template v-if="detail">
          <dl class="detail-list">
            <dt>价格</dt>
            <dd>¥{{ detail.price }}（{{ detail.currency }}）</dd>
            <dt>服务类型</dt>
            <dd>{{ detail.service_type }}</dd>
            <dt>有效期</dt>
            <dd>{{ detail.validity_description }}</dd>
            <dt>适用范围</dt>
            <dd>{{ detail.service_scope }}</dd>
            <dt>政策版本</dt>
            <dd>{{ detail.version }}</dd>
          </dl>
          <h3>包含权益</h3>
          <ul class="rights">
            <li v-for="right in detail.rights" :key="right">{{ right }}</li>
          </ul>
          <p class="rail-note">权益以平台展示为准，不构成对服务结果的承诺。</p>
          <button class="full-button" @click="detail = null; consultPlan(detail.id)">咨询该套餐</button>
        </template>
      </aside>
    </div>
  </section>
</template>
