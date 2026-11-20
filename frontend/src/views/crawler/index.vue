<template>
  <section class="page" data-module="crawler">
    <header class="page-head">
      <div>
        <h2>网页采集规则管理</h2>
        <p class="page-desc">
          维护采集规则，按规则从缓存页面样本抽取正文；明细保留来源网址、页码与抽取所用的规则，
          批次确认后进入采集去重。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记采集规则</button>
        <button class="btn" type="button" @click="exportRows">导出网页采集规则清单</button>
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
            <button class="link" type="button" @click="extractRule(row)">抽取正文</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无网页采集规则数据，可先登记采集规则</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条网页采集规则记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <section class="detail-block">
      <header class="detail-head">
        <h3>缓存页面样本</h3>
        <div class="detail-tools">
          <select v-model="sampleFilter" class="select" @change="reloadSamples">
            <option value="">全部规则</option>
            <option v-for="row in rows" :key="String(row.id)" :value="String(row['规则编号'])">
              {{ row['规则编号'] }}
            </option>
          </select>
          <button class="btn" type="button" @click="openSample">补录缓存样本</button>
        </div>
      </header>
      <table class="data-table">
        <thead>
          <tr>
            <th>样本编号</th><th>规则编号</th><th>来源网址</th><th>页码</th>
            <th>缓存状态</th><th>缓存时间</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="sample in samples" :key="String(sample.id)">
            <td>{{ sample['样本编号'] }}</td>
            <td>{{ sample['规则编号'] }}</td>
            <td class="cell-url">{{ sample['来源网址'] }}</td>
            <td>{{ sample['页码'] }}</td>
            <td>
              <span :class="['tag', sample['缓存状态'] === '就绪' ? 'ok' : 'warn']">{{ sample['缓存状态'] }}</span>
            </td>
            <td>{{ sample['缓存时间'] || '—' }}</td>
            <td class="row-actions">
              <button class="link" type="button" @click="openSample(sample)">补录</button>
            </td>
          </tr>
          <tr v-if="!samples.length">
            <td colspan="7" class="empty-state">还没有缓存样本，先补录一份再抽取</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="detail-block">
      <header class="detail-head">
        <h3>采集批次与页记录</h3>
        <div class="detail-tools">
          <select v-model="batchFilter" class="select" @change="reloadBatches">
            <option value="">全部状态</option>
            <option v-for="item in batchStatuses" :key="item" :value="item">{{ item }}</option>
          </select>
        </div>
      </header>
      <table class="data-table">
        <thead>
          <tr>
            <th>批次编号</th><th>规则编号</th><th>页面总数</th><th>已确认页数</th>
            <th>状态</th><th>说明</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="batch in batches" :key="String(batch['id'])">
            <td>{{ batch['批次编号'] }}</td>
            <td>{{ batch['规则编号'] }}</td>
            <td>{{ batch['页面总数'] }}</td>
            <td>{{ batch['已确认页数'] }}</td>
            <td><span :class="['tag', batchTagClass(batch)]">{{ batch['状态'] }}</span></td>
            <td class="cell-note">{{ batch['说明'] || '—' }}</td>
            <td class="row-actions">
              <button class="link" type="button" @click="toggleBatch(batch)">
                {{ expandedBatch === batch['批次编号'] ? '收起' : '查看页记录' }}
              </button>
              <button
                class="link"
                type="button"
                :disabled="batch['状态'] === '已确认'"
                @click="batchAction('确认批次', batch)"
              >
                确认批次
              </button>
              <button class="link" type="button" @click="batchAction('恢复抽取', batch)">恢复抽取</button>
            </td>
          </tr>
          <tr v-if="!batches.length">
            <td colspan="7" class="empty-state">还没有采集批次，先对一条规则执行抽取正文</td>
          </tr>
        </tbody>
      </table>

      <div v-if="expandedView" class="batch-panel">
        <div class="batch-panel-head">
          <strong>{{ expandedView['批次编号'] }}</strong>
          <span>可确认：{{ expandedView['可确认'] ? '是' : '否' }}</span>
          <span v-if="expandedView['阻断原因']?.length" class="error-text">
            阻断原因：{{ expandedView['阻断原因'].join('；') }}
          </span>
        </div>
        <table class="data-table">
          <thead>
            <tr><th>页码</th><th>来源网址</th><th>状态</th><th>明细编号</th><th>说明</th></tr>
          </thead>
          <tbody>
            <tr v-for="page in expandedView['页记录']" :key="`${page['页码']}-${page['样本编号']}`">
              <td>{{ page['页码'] }}</td>
              <td class="cell-url">{{ page['来源网址'] }}</td>
              <td><span :class="['tag', pageTagClass(page)]">{{ page['状态'] }}</span></td>
              <td>{{ page['明细编号'] || '—' }}</td>
              <td class="cell-note">{{ page['说明'] || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="detail-block">
      <header class="detail-head">
        <h3>网页正文明细</h3>
        <div class="detail-tools">
          <select v-model="detailFilter" class="select" @change="reloadDetails">
            <option value="">全部状态</option>
            <option value="待确认">待确认</option>
            <option value="已确认">已确认</option>
          </select>
          <button class="btn" type="button" @click="exportDetails">导出正文明细</button>
        </div>
      </header>
      <table class="data-table">
        <thead>
          <tr>
            <th>明细编号</th><th>批次编号</th><th>规则编号</th><th>来源网址</th>
            <th>页码</th><th>正文选择器</th><th>状态</th><th>正文</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="detail in details" :key="String(detail.id)">
            <td>{{ detail['明细编号'] }}</td>
            <td>{{ detail['批次编号'] }}</td>
            <td>{{ detail['规则编号'] }}</td>
            <td class="cell-url">{{ detail['来源网址'] }}</td>
            <td>{{ detail['页码'] }}</td>
            <td class="cell-code">{{ detail['正文选择器'] }}</td>
            <td><span :class="['tag', detail['状态'] === '已确认' ? 'ok' : 'warn']">{{ detail['状态'] }}</span></td>
            <td class="cell-body">{{ detail['正文'] }}</td>
          </tr>
          <tr v-if="!details.length">
            <td colspan="8" class="empty-state">暂无网页正文明细</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot">
        <span>共 {{ detailTotal }} 条网页正文明细，只有「已确认」的正文会被采集去重读取</span>
      </footer>
    </section>

    <Teleport to="body">
      <div v-if="showCreateModal" class="modal-overlay" @click.self="showCreateModal = false">
        <div class="modal-card">
          <header class="modal-head">
            <h3>登记采集规则</h3>
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

      <div v-if="showSampleModal" class="modal-overlay" @click.self="showSampleModal = false">
        <div class="modal-card">
          <header class="modal-head">
            <h3>{{ sampleForm.id ? `补录缓存样本 ${sampleForm['样本编号']}` : '登记缓存样本' }}</h3>
            <button class="btn ghost" type="button" @click="showSampleModal = false">&times;</button>
          </header>
          <form class="modal-body" @submit.prevent="submitSample">
            <label v-if="!sampleForm.id" class="modal-field">
              <span>规则编号</span>
              <input v-model="sampleForm['规则编号']" placeholder="例如 CRAW-0001" required />
            </label>
            <label v-if="!sampleForm.id" class="modal-field">
              <span>来源网址</span>
              <input v-model="sampleForm['来源网址']" placeholder="https://..." required />
            </label>
            <label v-if="!sampleForm.id" class="modal-field">
              <span>页码</span>
              <input v-model="sampleForm['页码']" type="number" min="1" required />
            </label>
            <label class="modal-field">
              <span>页面内容（缓存 HTML）</span>
              <textarea v-model="sampleForm['页面内容']" rows="6" placeholder="粘贴缓存页面内容"></textarea>
            </label>
            <label class="modal-field">
              <span>缓存状态</span>
              <select v-model="sampleForm['缓存状态']" class="select">
                <option value="就绪">就绪</option>
                <option value="缓存缺失">缓存缺失</option>
              </select>
            </label>
            <footer class="modal-foot">
              <button class="btn" type="button" @click="showSampleModal = false">取消</button>
              <button class="btn primary" type="submit">保存缓存</button>
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

const ENDPOINT = '/api/crawler'
const columns = ["规则编号", "目标网址", "页面选择器", "正文选择器", "翻页参数", "抓取频率", "字符编码", "去重策略"]
const actions = ["提交测试", "发布规则", "停用规则"]
const statuses = ["草稿", "测试中", "已发布", "已停用"]
const batchStatuses = ["待确认", "已确认", "已中断", "已终止"]
const stats = [
  { label: "规则总数", value: 0 },
  { label: "已发布规则", value: 0 },
  { label: "待确认正文", value: 0 },
]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreateModal = ref(false)
const createFields = ref<Record<string, string>>({})

const samples = ref<Row[]>([])
const sampleFilter = ref('')
const showSampleModal = ref(false)
const sampleForm = ref<Row>({})

const batches = ref<Row[]>([])
const batchFilter = ref('')
const expandedBatch = ref('')
const expandedView = ref<Row | null>(null)

const details = ref<Row[]>([])
const detailTotal = ref(0)
const detailFilter = ref('')

function updateStats() {
  stats[0].value = rows.value.length
  stats[1].value = rows.value.filter((row) => row.status === '已发布').length
}

function batchTagClass(row: Row) {
  if (row['状态'] === '已确认') return 'ok'
  if (row['状态'] === '已中断') return 'warn'
  if (row['状态'] === '已终止') return 'muted'
  return 'info'
}

function pageTagClass(row: Row) {
  if (row['状态'] === '已确认') return 'ok'
  if (row['状态'] === '已抽取') return 'info'
  if (row['状态'] === '已释放') return 'muted'
  return 'warn'
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

function exportDetails() {
  const query = new URLSearchParams(detailFilter.value ? { status: detailFilter.value } : {}).toString()
  window.open(`${ENDPOINT}/details/export?${query}`, '_blank')
}

function openCreate() {
  errorMessage.value = ''
  createFields.value = {}
  showCreateModal.value = true
}

function openSample(sample?: Row) {
  errorMessage.value = ''
  sampleForm.value = sample
    ? { id: sample.id, 样本编号: sample['样本编号'], 页面内容: '', 缓存状态: sample['缓存状态'] }
    : { 缓存状态: '就绪' }
  showSampleModal.value = true
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

async function submitSample() {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/samples`, {
      method: 'POST',
      body: JSON.stringify({ values: sampleForm.value }),
    })
    const data = await response.json()
    if (!response.ok || !data.ok) {
      throw new Error(data.message || '缓存样本保存失败')
    }
    showSampleModal.value = false
    await reloadSamples()
    await reloadBatches()
  } catch (error) {
    setError(error, '缓存样本保存失败')
  }
}

async function extractRule(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/extract`, {
      method: 'POST',
      body: JSON.stringify({ values: {} }),
    })
    const data = await response.json()
    if (!response.ok || !data.ok) {
      throw new Error(data.message || '正文抽取未执行')
    }
    errorMessage.value = data.message
    await reloadBatches()
    await reloadDetails()
  } catch (error) {
    setError(error, '正文抽取未执行')
  }
}

async function batchAction(action: string, batch: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/batches/${batch.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const data = await response.json()
    if (!response.ok || !data.ok) {
      throw new Error(data.message || `${action}未生效`)
    }
    errorMessage.value = data.message
    await reloadBatches()
    await reloadDetails()
    if (expandedBatch.value === batch['批次编号']) {
      const refreshed = await (await request(`${ENDPOINT}/batches/${batch.id}`)).json()
      expandedView.value = refreshed
    }
    if (action === '确认批次') {
      await reload()
    }
  } catch (error) {
    setError(error, `${action}未生效`)
  }
}

async function toggleBatch(batch: Row) {
  if (expandedBatch.value === batch['批次编号']) {
    expandedBatch.value = ''
    expandedView.value = null
    return
  }
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/batches/${batch.id}`)
    if (!response.ok) {
      throw new Error('批次页记录读取失败')
    }
    expandedView.value = await response.json()
    expandedBatch.value = batch['批次编号']
  } catch (error) {
    setError(error, '批次页记录读取失败')
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const data = await response.json()
    if (!response.ok || !data.ok) {
      throw new Error(data.message || '网页采集规则动作未生效，请稍后重试')
    }
    errorMessage.value = data.message
    await reload()
    await reloadBatches()
  } catch (error) {
    setError(error, '网页采集规则操作失败')
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('采集规则列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    updateStats()
  } catch (error) {
    setError(error, '网页采集规则列表读取失败')
  }
}

async function reloadSamples() {
  const query = new URLSearchParams(sampleFilter.value ? { rule_code: sampleFilter.value } : {}).toString()
  try {
    const payload = await (await request(`${ENDPOINT}/samples?${query}`)).json()
    samples.value = payload.items ?? []
  } catch (error) {
    setError(error, '缓存样本读取失败')
  }
}

async function reloadBatches() {
  const query = new URLSearchParams(batchFilter.value ? { status: batchFilter.value } : {}).toString()
  try {
    const payload = await (await request(`${ENDPOINT}/batches?${query}`)).json()
    batches.value = payload.items ?? []
    stats[2].value = batches.value
      .filter((batch: Row) => batch['状态'] !== '已确认')
      .reduce((sum: number, batch: Row) => sum + Number(batch['待处理页数'] ?? 0), 0)
  } catch (error) {
    setError(error, '采集批次读取失败')
  }
}

async function reloadDetails() {
  const query = new URLSearchParams(detailFilter.value ? { status: detailFilter.value } : {}).toString()
  try {
    const payload = await (await request(`${ENDPOINT}/details?${query}&size=50`)).json()
    details.value = payload.items ?? []
    detailTotal.value = payload.total ?? details.value.length
  } catch (error) {
    setError(error, '网页正文明细读取失败')
  }
}

onMounted(async () => {
  await reload()
  await reloadSamples()
  await reloadBatches()
  await reloadDetails()
})
</script>

<style scoped>
.detail-block { margin-top: 22px; }
.detail-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.detail-head h3 { margin: 0; font-size: 15px; }
.detail-tools { display: flex; gap: 8px; align-items: center; }
.select { padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; background: #fff; }
.cell-url { max-width: 260px; word-break: break-all; }
.cell-note { max-width: 320px; }
.cell-code { font-family: ui-monospace, monospace; font-size: 12px; }
.cell-body { max-width: 420px; line-height: 1.5; }
.batch-panel { margin-top: 10px; padding: 10px; background: #fff; border: 1px solid var(--border); border-radius: 8px; }
.batch-panel-head { display: flex; gap: 14px; flex-wrap: wrap; align-items: center; font-size: 13px; margin-bottom: 8px; }
.tag { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 12px; border: 1px solid transparent; }
.tag.ok { background: #ecfdf3; color: #067647; border-color: #abefc6; }
.tag.warn { background: #fff6ed; color: #b54708; border-color: #f9dbaf; }
.tag.info { background: #eff8ff; color: #175cd3; border-color: #b2ddff; }
.tag.muted { background: #f2f4f7; color: #475467; border-color: #d0d5dd; }
textarea { padding: 7px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; font-family: ui-monospace, monospace; resize: vertical; }
</style>
