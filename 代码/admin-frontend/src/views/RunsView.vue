<script setup>
import { onMounted } from 'vue'
import {
  closeRun,
  inspectRun,
  loadRuns,
  outcomeLabel,
  state,
} from '../store'

onMounted(() => {
  if (!state.runs.length) loadRuns().catch((cause) => (state.error = cause.message))
})

function format(value) {
  if (!value) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(value))
}

function refresh() {
  loadRuns().catch((cause) => (state.error = cause.message))
}
</script>

<template>
  <section>
    <header class="page-heading">
      <div>
        <p class="eyebrow">AI RUN AUDIT</p>
        <h1>AI 运行记录</h1>
        <p>Run 完成仅代表模型计算结束；Customer 是否接纳以“接纳结果”为准。</p>
      </div>
      <div class="head-actions">
        <select v-model="state.runFilter" @change="refresh">
          <option value="">全部状态</option>
          <option value="COMPLETED">COMPLETED</option>
          <option value="DECISION_PREPARED">DECISION_PREPARED</option>
          <option value="HANDED_OFF">HANDED_OFF</option>
          <option value="FAILED">FAILED</option>
          <option value="SUPERSEDED">SUPERSEDED</option>
        </select>
        <button class="ghost-button" @click="refresh">刷新</button>
      </div>
    </header>

    <div v-if="state.runtime" class="runtime-banner">
      <span :class="['status-chip', state.runtime.runtime_mode === 'FIXTURE' ? 'attention' : 'done']">
        {{ state.runtime.runtime_mode }}
      </span>
      <span>模型 {{ state.runtime.model_name || '未配置' }}</span>
      <span>API 版本 {{ state.runtime.model_artifact_version || '—' }}</span>
      <span>{{ state.runtime.detail }}</span>
    </div>

    <div class="metric-row">
      <article class="metric-card">
        <span>Run 总数</span>
        <strong>{{ state.metrics.ai.agent_runs || 0 }}</strong>
      </article>
      <article class="metric-card">
        <span>平均耗时</span>
        <strong>{{ Math.round(state.metrics.ai.average_latency_ms || 0) }} ms</strong>
      </article>
      <article class="metric-card">
        <span>等待人工</span>
        <strong>{{ state.metrics.service.queued || 0 }}</strong>
      </article>
      <article class="metric-card">
        <span>人工处理中</span>
        <strong>{{ state.metrics.service.human || 0 }}</strong>
      </article>
    </div>

    <div v-if="state.runsLoading" class="empty-state"><p>正在加载运行记录…</p></div>

    <div v-else class="panel">
      <table class="run-table">
        <thead>
          <tr>
            <th>Run</th>
            <th>状态</th>
            <th>流程</th>
            <th>模型 / 模式</th>
            <th>版本</th>
            <th>耗时</th>
            <th>接纳结果</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="run in state.runs" :key="run.id">
            <td>
              <strong>{{ run.id }}</strong>
              <small>{{ run.turn_id }}</small>
            </td>
            <td><span :class="['status-chip', run.state === 'FAILED' ? 'cancelled' : 'active']">{{ run.state }}</span></td>
            <td>{{ run.flow_type || '—' }}</td>
            <td>
              {{ run.model_name }}
              <small>{{ run.runtime_mode }}</small>
            </td>
            <td>
              <small>合同 {{ run.contract_version }}</small>
              <small>流程 {{ run.flow_definition_version }}</small>
              <small>输入 v{{ run.input_revision ?? '—' }} / 状态 v{{ run.flow_state_version ?? '—' }}</small>
            </td>
            <td>{{ run.latency_ms ?? '—' }} ms</td>
            <td>
              <span :class="['status-chip', run.customer_outcome === 'ACCEPTED' ? 'done' : run.customer_outcome === 'REJECTED' ? 'attention' : 'subtle']">
                {{ outcomeLabel(run.customer_outcome) }}
              </span>
            </td>
            <td><button class="ghost-button small" @click="inspectRun(run)">查看</button></td>
          </tr>
          <tr v-if="!state.runs.length">
            <td colspan="8" class="table-empty">暂无运行记录</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="state.selectedRun" class="drawer-mask" @click.self="closeRun">
      <aside class="drawer wide">
        <header>
          <div>
            <strong>{{ state.selectedRun.run.id }}</strong>
            <small>{{ state.selectedRun.run.state }} · {{ outcomeLabel(state.selectedRun.run.customer_outcome) }}</small>
          </div>
          <button class="icon-button" aria-label="关闭" @click="closeRun">×</button>
        </header>

        <section class="drawer-block">
          <h3>版本与输入</h3>
          <dl class="detail-list">
            <dt>模型</dt>
            <dd>{{ state.selectedRun.run.model_name }}（{{ state.selectedRun.run.model_artifact_version }}）</dd>
            <dt>运行模式</dt>
            <dd>{{ state.selectedRun.run.runtime_mode }}</dd>
            <dt>提示版本</dt>
            <dd>{{ state.selectedRun.run.prompt_version }}</dd>
            <dt>合同版本</dt>
            <dd>{{ state.selectedRun.run.contract_version }}</dd>
            <dt>流程版本</dt>
            <dd>{{ state.selectedRun.run.flow_definition_version }}</dd>
            <dt>输入 / 状态版本</dt>
            <dd>v{{ state.selectedRun.run.input_revision }} / v{{ state.selectedRun.run.flow_state_version }}</dd>
            <dt>Token</dt>
            <dd>输入 {{ state.selectedRun.run.input_tokens }} · 输出 {{ state.selectedRun.run.output_tokens }}</dd>
            <dt>时间</dt>
            <dd>{{ format(state.selectedRun.run.started_at) }} → {{ format(state.selectedRun.run.finished_at) }}</dd>
          </dl>
        </section>

        <section class="drawer-block">
          <h3>模型决策（PolicyDecision）</h3>
          <pre>{{ JSON.stringify(state.selectedRun.run.policy_decision, null, 2) }}</pre>
        </section>

        <section class="drawer-block">
          <h3>执行计划</h3>
          <pre>{{ JSON.stringify(state.selectedRun.run.execution_plan, null, 2) }}</pre>
        </section>

        <section class="drawer-block">
          <h3>最终结果与流程补丁</h3>
          <pre>{{ JSON.stringify(state.selectedRun.run.outcome, null, 2) }}</pre>
        </section>

        <section class="drawer-block">
          <h3>工具调用</h3>
          <article v-for="call in state.selectedRun.tool_calls" :key="call.tool_call_id" class="tool-call">
            <div>
              <strong>{{ call.tool_name }}</strong>
              <span :class="['status-chip', call.success ? 'done' : 'cancelled']">
                {{ call.success ? '成功' : '失败' }}
              </span>
              <small>{{ call.latency_ms }} ms</small>
            </div>
            <pre>{{ JSON.stringify(call.arguments, null, 2) }}</pre>
            <details>
              <summary>查看脱敏结果</summary>
              <pre>{{ JSON.stringify(call.result, null, 2) }}</pre>
            </details>
          </article>
          <p v-if="!state.selectedRun.tool_calls.length" class="muted">本次 Run 没有工具调用。</p>
        </section>

        <p v-if="state.selectedRun.run.error" class="error-text">
          错误：{{ state.selectedRun.run.error }}
        </p>
      </aside>
    </div>
  </section>
</template>
