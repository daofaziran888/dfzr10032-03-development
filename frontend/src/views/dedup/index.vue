<template>
  <section class="page" data-module="dedup">
    <header class="page-head">
      <div>
        <h2>采集去重管理</h2>
        <p class="page-desc">
          维护去重策略；执行去重时读取网页正文明细里已确认的正文，按来源网址与正文识别重复页面，
          并在去重结果里记录重复页对应的保留记录。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记去重策略</button>
        <button class="btn" type="button" @click="exportRows">导出采集去重清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] || '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button class="link" type="button" @click="previewDedup(row)">预演去重</button>
            <button class="link" type="button" @click="runDedup(row)">执行去重</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无采集去重数据，可先登记去重策略</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条采集去重记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-else-if="noticeMessage" class="notice-text">{{ noticeMessage }}</span>
    </footer>

    <section class="detail-block">
      <header class="detail-head">
        <h3>去重结果</h3>
        <div class="detail-tools">
          <select v-model="resultFilter" class="select" @change="reloadResults">
            <option value="">全部策略</option>
            <option v-for="row in rows" :key="String(row.id)" :value="String(row['策略编号'])">
              {{ row['策略编号'] }}
            </option>
          </select>
        </div>
      </header>
      <table class="data-table">
        <thead>
          <tr>
            <th>结果编号</th><th>策略编号</th><th>来源网址</th><th>页码</th>
            <th>保留明细编号</th><th>重复明细编号</th><th>保留来源网址</th><th>结论</th><th>处理时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in results" :key="String(item.id)">
            <td>{{ item['结果编号'] }}</td>
            <td>{{ item['策略编号'] }}</td>
            <td class="cell-url">{{ item['来源网址'] }}</td>
            <td>{{ item['页码'] }}</td>
            <td>{{ item['保留明细编号'] }}</td>
            <td>{{ item['重复明细编号'] }}</td>
            <td class="cell-url">{{ item['保留来源网址'] }}</td>
            <td><span class="tag warn">{{ item['结论'] }}</span></td>
            <td>{{ item['处理时间'] }}</td>
          </tr>
          <tr v-if="!results.length">
            <td colspan="9" class="empty-state">还没有去重结果，确认正文后执行去重即可生成</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot">
        <span>共 {{ resultTotal }} 条去重结果；保留明细可在网页采集规则的「网页正文明细」中回查</span>
      </footer>
    </section>

    <Teleport to="body">
      <div v-if="showCreateModal" class="modal-overlay" @click.self="showCreateModal = false">
        <div class="modal-card">
          <header class="modal-head">
            <h3>登记去重策略</h3>
            <button class="btn ghost" type="button" @click="showCreateModal = false">&times;</button>
          </header>
          <form class="modal-body" @submit.prevent="submitCreate">
            <label v-for="field in columns.slice(0, 3)" :key="field" class="modal-field">
              <span>{{ field }}</span>
              <input v-model="createFields[field]" :placeholder="`请输入${field}`" required />
            </label>
            <footer class="modal-foot">
              <button class="btn" type="button" @click="showCreateModal = false">取消</button>
              <button class="btn primary" type="submit">提交登记</button>
            </footer>
          </form>
        </div>
      </div>
    </Teleport>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>

const ENDPOINT = '/api/dedup'
const columns = ["策略编号", "输入来源", "去重键", "排序字段", "保留策略", "时间窗口", "重复阈值", "策略状态"]
const actions = ["提交配置", "启用策略", "停用策略"]
const statuses = ["草稿", "已配置", "生效中", "已停用"]
const stats = [
  { label: "策略总数", value: 0 },
  { label: "生效中策略", value: 0 },
  { label: "去重结果", value: 0 },
]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const noticeMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreateModal = ref(false)
const createFields = ref<Record<string, string>>({})

const results = ref<Row[]>([])
const resultTotal = ref(0)
const resultFilter = ref('')

function updateStats() {
  stats[0].value = rows.value.length
  stats[1].value = rows.value.filter((row) => row.status === '生效中').length
}

function setError(error: unknown, fallback: string) {
  errorMessage.value = error instanceof Error ? error.message : fallback
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = ''
  createFields.value = {}
  showCreateModal.value = true
}

async function submitCreate() {
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: createFields.value }),
    })
    if (!response.ok) {
      const data = await response.json()
      throw new Error(data.message || '登记失败')
    }
    showCreateModal.value = false
    await reload()
  } catch (error) {
    setError(error, '登记失败')
  }
}

async function previewDedup(row: Row) {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/preview`, {
      method: 'POST',
      body: JSON.stringify({ values: {} }),
    })
    const data = await response.json()
    if (!response.ok || !data.ok) {
      throw new Error(data.message || '预演未执行')
    }
    noticeMessage.value = `${data.message}；不会写去重结果`
  } catch (error) {
    setError(error, '预演未执行')
  }
}

async function runDedup(row: Row) {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/run`, {
      method: 'POST',
      body: JSON.stringify({ values: {} }),
    })
    const data = await response.json()
    if (!response.ok || !data.ok) {
      throw new Error(data.message || '去重未执行')
    }
    noticeMessage.value = data.message
    await reloadResults()
  } catch (error) {
    setError(error, '去重未执行')
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const data = await response.json()
    if (!response.ok || !data.ok) {
      throw new Error(data.message || '采集去重动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    setError(error, '采集去重操作失败')
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('去重策略列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    updateStats()
  } catch (error) {
    setError(error, '采集去重列表读取失败')
  }
}

async function reloadResults() {
  const query = new URLSearchParams(resultFilter.value ? { strategy_code: resultFilter.value } : {}).toString()
  try {
    const payload = await (await request(`${ENDPOINT}/results?${query}`)).json()
    results.value = payload.items ?? []
    resultTotal.value = payload.total ?? results.value.length
    stats[2].value = resultTotal.value
  } catch (error) {
    setError(error, '去重结果读取失败')
  }
}

onMounted(async () => {
  await reload()
  await reloadResults()
})
</script>

<style scoped>
.detail-block { margin-top: 22px; }
.detail-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.detail-head h3 { margin: 0; font-size: 15px; }
.detail-tools { display: flex; gap: 8px; align-items: center; }
.select { padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; background: #fff; }
.cell-url { max-width: 260px; word-break: break-all; }
.notice-text { color: #067647; }
.tag { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 12px; }
.tag.warn { background: #fff6ed; color: #b54708; }
</style>
