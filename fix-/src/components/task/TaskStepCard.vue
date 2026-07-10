<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  ArrowDown,
  ArrowUp,
  ChatDotRound,
  Check,
  Clock,
  Document,
  Headset,
  Picture,
  UploadFilled,
  VideoPause,
  Warning,
} from '@element-plus/icons-vue'
import { executeStep, forceCompleteStep, reopenStep, rollbackToStep } from '@/api/maintenanceTask'
import { uploadImage } from '@/api/user'
import { notifyStore } from '@/stores/notifyStore'
import { stepStatus, stepActionable } from '@/constants/taskStatus'

const props = defineProps({
  step: { type: Object, required: true },
  taskId: { required: true },
  active: { type: Boolean, default: false },   // 是否当前应执行的步骤
  executing: { type: Boolean, default: false },// 任务是否处于 EXECUTING
  reading: { type: Boolean, default: false },  // 跟读模式下：是否正在念这一步（高亮）
})
const emit = defineEmits(['submitted', 'reopened', 'chat', 'read-along'])

const exec = reactive({ note: '', images: [], confirmed: false })
const uploading = ref(false)
const submitting = ref(false)
const expanded = ref(false)

const st = computed(() => stepStatus(props.step.status))
const canAct = computed(() => props.executing && props.active && stepActionable(props.step.status))
const rejected = computed(() => props.step.status === 'AI_REJECTED')
const done = computed(() => ['AI_PASSED', 'COMPLETED', 'SKIPPED'].includes(props.step.status))
const canReopen = computed(() => props.executing && ['SUBMITTED', 'AI_PASSED', 'COMPLETED', 'SKIPPED'].includes(props.step.status))
// 「从此步回退」：任务执行中，且该步骤已完成/提交/通过（有东西可重置）
const canRollback = computed(() => props.executing && ['SUBMITTED', 'COMPLETED', 'AI_PASSED'].includes(props.step.status))
const showBody = computed(() => canAct.value || rejected.value || expanded.value)
const requirementCount = computed(() =>
  [props.step.requirePhoto, props.step.requireNote, props.step.isCheckpoint].filter(Boolean).length,
)

// 置信度后端返回字符串「高/中/低」，映射为样式等级
function confLevel(v) {
  const s = String(v || '').trim()
  if (s.includes('高')) return 'high'
  if (s.includes('中')) return 'mid'
  if (s.includes('低')) return 'low'
  return 'mid'
}

watch(canAct, (a) => { if (a) expanded.value = true }, { immediate: true })

async function onPickFiles(e) {
  const files = Array.from(e.target.files || [])
  e.target.value = ''
  if (!files.length) return
  uploading.value = true
  try {
    for (const f of files) {
      const res = await uploadImage(f)
      const url = res?.data || res?.url
      if (url) exec.images.push(url)
    }
  } catch (err) { ElMessage.error('图片上传失败：' + (err.message || '')) }
  finally { uploading.value = false }
}
function removeImage(i) { exec.images.splice(i, 1) }

async function submit() {
  const s = props.step
  if (s.requirePhoto && !exec.images.length) { ElMessage.warning('该步骤要求上传照片'); return }
  if (s.requireNote && !exec.note.trim()) { ElMessage.warning('该步骤要求填写备注'); return }
  if (s.isCheckpoint && !exec.confirmed) { ElMessage.warning('请先确认检查点'); return }
  submitting.value = true
  try {
    await executeStep(props.taskId, s.id, {
      images: exec.images, note: exec.note.trim(), checkpointConfirmed: exec.confirmed,
    })
    notifyStore.trackJob({ key: 'step:' + s.id, kind: 'step', refId: s.id, taskId: props.taskId, title: '步骤AI验证：' + (s.title || '') })
    ElMessage.success('已提交，AI 验证中…')
    emit('submitted')
  } catch (err) { ElMessage.error('提交失败：' + (err.message || '')) }
  finally { submitting.value = false }
}

async function forceComplete() {
  submitting.value = true
  try {
    await forceCompleteStep(props.taskId, props.step.id, exec.note.trim() || '工人确认无误')
    ElMessage.success('已强制完成该步骤')
    emit('submitted')
  } catch (err) { ElMessage.error('操作失败：' + (err.message || '')) }
  finally { submitting.value = false }
}

async function reopen() {
  submitting.value = true
  try {
    await reopenStep(props.taskId, props.step.id, exec.note.trim() || '工人要求重新执行')
    exec.note = ''
    exec.images = []
    exec.confirmed = false
    expanded.value = true
    ElMessage.success('已重新打开该步骤')
    emit('reopened')
  } catch (err) { ElMessage.error('操作失败：' + (err.message || '')) }
  finally { submitting.value = false }
}

async function rollback() {
  submitting.value = true
  try {
    await rollbackToStep(props.taskId, props.step.id, '工人要求从此步重做')
    ElMessage.success(`已回退到第 ${props.step.sortOrder} 步，该步骤及之后步骤已重置`)
    emit('reopened')   // 复用 reopened 事件触发父组件刷新
  } catch (err) { ElMessage.error('回退失败：' + (err.message || '')) }
  finally { submitting.value = false }
}
</script>

<template>
  <article
    class="step-card-shell"
    :class="{ active: canAct, done, rejected, reading }"
    :data-step-id="String(step.id)"
  >
    <span class="timeline-node" aria-hidden="true">
      <el-icon v-if="done"><Check /></el-icon>
      <i v-else-if="step.status === 'SUBMITTED'" class="node-spinner" />
      <b v-else>{{ String(step.sortOrder || 0).padStart(2, '0') }}</b>
    </span>

    <div class="step-card">
      <header class="step-header">
        <div class="step-title">
          <span class="step-kicker">
            STEP {{ String(step.sortOrder || 0).padStart(2, '0') }}
            <em v-if="canAct">CURRENT OPERATION</em>
          </span>
          <h3>{{ step.title || '未命名检修步骤' }}</h3>
        </div>

        <span class="step-status" :style="{ color: st.color, background: st.bg }">
          <i v-if="st.spin" class="status-spinner" />
          {{ st.label }}
        </span>
      </header>

      <div class="step-readout">
        <span v-if="step.estimatedMinutes"><el-icon><Clock /></el-icon> 约 {{ step.estimatedMinutes }} 分钟</span>
        <span v-if="step.requirePhoto"><el-icon><Picture /></el-icon> 需现场照片</span>
        <span v-if="step.requireNote"><el-icon><Document /></el-icon> 需执行备注</span>
        <span v-if="step.isCheckpoint"><el-icon><Check /></el-icon> 合规检查点</span>
        <span v-if="!requirementCount && !step.estimatedMinutes">标准作业步骤</span>

        <div class="step-tools">
          <button type="button" class="ask-button" :class="{ reading }" @click="emit('read-along', step)">
            <el-icon><VideoPause v-if="reading" /><Headset v-else /></el-icon>
            {{ reading ? '停止跟读' : '从此步跟读' }}
          </button>
          <button type="button" class="ask-button" @click="emit('chat', step)">
            <el-icon><ChatDotRound /></el-icon> 问 AI
          </button>
          <button v-if="canReopen" type="button" class="ask-button" :disabled="submitting" @click="reopen">
            重新执行
          </button>
          <button v-if="canRollback" type="button" class="ask-button rollback-btn" :disabled="submitting" @click="rollback" :title="`重置第 ${step.sortOrder} 步及之后所有步骤`">
            从此步回退
          </button>
          <button
            v-if="!canAct"
            type="button"
            class="detail-toggle"
            :aria-expanded="showBody"
            @click="expanded = !expanded"
          >
            {{ showBody ? '收起' : '详情' }}
            <el-icon><ArrowUp v-if="showBody" /><ArrowDown v-else /></el-icon>
          </button>
        </div>
      </div>

      <div v-if="showBody" class="step-body">
        <p v-if="step.content" class="step-content">{{ step.content }}</p>

        <div v-if="step.safetyNote" class="safety-note">
          <span><el-icon><Warning /></el-icon> 安全提示</span>
          <p>{{ step.safetyNote }}</p>
        </div>

        <div v-if="step.isCheckpoint && (step.checkpointItems || []).length" class="checkpoint">
          <div class="checkpoint-head">
            <span>COMPLIANCE CHECK</span>
            <b>{{ step.checkpointItems.length }} 项</b>
          </div>
          <ul>
            <li v-for="(item, index) in step.checkpointItems" :key="index">
              <i>{{ String(index + 1).padStart(2, '0') }}</i>
              {{ item }}
            </li>
          </ul>
        </div>

        <div v-if="(step.images || []).length" class="submitted-images">
          <img v-for="(url, index) in step.images" :key="index" :src="url" :alt="`步骤证据图片 ${index + 1}`" />
        </div>
        <p v-if="step.note && !canAct" class="submitted-note">
          <b>执行备注</b>{{ step.note }}
        </p>

        <!-- AI 验证结果（置信度为字符串 高/中/低） -->
        <div
          v-if="step.aiPass !== null && step.aiPass !== undefined"
          class="ai-verdict"
          :class="step.aiPass ? 'passed' : 'failed'"
        >
          <span class="verdict-icon">
            <el-icon v-if="step.aiPass"><Check /></el-icon>
            <el-icon v-else><Warning /></el-icon>
          </span>
          <span class="verdict-copy">
            <b>
              {{ step.aiPass ? 'AI 验证通过' : 'AI 验证未通过' }}
              <em
                v-if="step.aiConfidence != null && step.aiConfidence !== ''"
                class="conf"
                :class="'conf-' + confLevel(step.aiConfidence)"
              >置信度 {{ step.aiConfidence }}</em>
            </b>
            <p v-if="step.aiReason">{{ step.aiReason }}</p>
          </span>
        </div>

        <!-- 执行面板（仅当前步骤、任务执行中） -->
        <div v-if="canAct" class="execution-panel">
          <div class="execution-heading">
            <div>
              <span>FIELD EVIDENCE</span>
              <h4>提交现场执行证据</h4>
            </div>
            <small>完成必填项后提交 AI 验证</small>
          </div>

          <div class="execution-grid">
            <div class="upload-zone">
              <div v-for="(url, index) in exec.images" :key="index" class="upload-preview">
                <img :src="url" :alt="`待提交图片 ${index + 1}`" />
                <button type="button" aria-label="移除图片" @click="removeImage(index)">×</button>
              </div>
              <label class="upload-button" :class="{ busy: uploading }">
                <input type="file" accept="image/*" multiple hidden @change="onPickFiles" />
                <el-icon><UploadFilled /></el-icon>
                <span>{{ uploading ? '上传中' : '添加照片' }}</span>
              </label>
            </div>

            <el-input
              v-model="exec.note"
              type="textarea"
              :rows="3"
              resize="none"
              placeholder="记录实际处理、仪表读数、异常现象或补充说明"
            />
          </div>

          <label v-if="step.isCheckpoint" class="checkpoint-confirm">
            <input v-model="exec.confirmed" type="checkbox" />
            <span>
              <b>确认合规检查点</b>
              <small>我已核对并确认上述检查项全部满足现场作业要求</small>
            </span>
          </label>

          <div class="execution-actions">
            <span>
              {{ step.requirePhoto ? '照片必填' : '照片选填' }} · {{ step.requireNote ? '备注必填' : '备注选填' }}
            </span>
            <button
              v-if="rejected"
              type="button"
              class="force-button"
              :disabled="submitting"
              @click="forceComplete"
            >
              强制完成
            </button>
            <button type="button" class="submit-button" :disabled="submitting || uploading" @click="submit">
              {{ submitting ? '提交中…' : rejected ? '重新提交验证' : '提交本步骤' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </article>
</template>

<style scoped>
.step-card-shell {
  position: relative;
  display: grid;
  grid-template-columns: 55px minmax(0, 1fr);
  min-width: 0;
  will-change: transform, opacity;
}

.timeline-node {
  position: relative;
  z-index: 2;
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  align-self: start;
  justify-self: center;
  margin-top: 15px;
  border: 4px solid var(--plaza-bg);
  border-radius: 10px;
  color: var(--plaza-text-muted);
  background: var(--plaza-bg-card);
  box-shadow: 0 0 0 1px var(--plaza-border);
}

.timeline-node b { font-family: var(--font-mono); font-size: 9px; }

.step-card-shell.active .timeline-node {
  color: #fff;
  background: var(--plaza-accent);
  box-shadow: 0 0 0 1px var(--plaza-accent), 0 0 0 6px var(--plaza-accent-soft);
}

.step-card-shell.done .timeline-node {
  color: #fff;
  background: #5e8c3e;
  box-shadow: 0 0 0 1px #4e7a32;
}

.node-spinner,
.status-spinner {
  display: inline-block;
  border: 2px solid currentColor;
  border-top-color: transparent;
  border-radius: 50%;
  animation: step-spin 0.8s linear infinite;
}
.node-spinner { width: 12px; height: 12px; color: var(--plaza-accent); }
.status-spinner { width: 8px; height: 8px; }
@keyframes step-spin { to { transform: rotate(360deg); } }

.step-card {
  min-width: 0;
  overflow: hidden;
  border: 1px solid var(--plaza-border);
  border-radius: 13px;
  background: var(--plaza-bg-card);
  box-shadow: var(--plaza-shadow-organic);
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}
.step-card-shell.active .step-card {
  border-color: var(--plaza-accent);
  box-shadow: 0 10px 28px var(--plaza-accent-soft-strong);
}
.step-card-shell.active .step-card::before {
  display: block;
  height: 3px;
  background: linear-gradient(90deg, var(--plaza-accent), var(--plaza-accent) 48%, transparent);
  content: '';
}
.step-card-shell.done .step-card { background: linear-gradient(180deg, var(--plaza-bg-card), var(--plaza-bg-card)); }

/* 跟读模式：正在念的步骤——左侧高亮边 + 柔光，区别于"当前执行"态 */
.step-card-shell.reading .step-card {
  border-color: var(--plaza-accent);
  border-left: 3px solid var(--plaza-accent);
  box-shadow: 0 0 0 3px var(--plaza-accent-soft), var(--plaza-shadow-organic);
}
.step-card-shell.reading .timeline-node {
  color: #fff;
  background: var(--plaza-accent);
  box-shadow: 0 0 0 1px var(--plaza-accent), 0 0 0 6px var(--plaza-accent-soft);
}

.step-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  padding: 14px 15px 7px;
}
.step-title { min-width: 0; }
.step-kicker {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--plaza-text-muted);
  font-family: var(--font-mono);
  font-size: 8px;
  font-weight: 800;
  letter-spacing: 0.12em;
}
.step-kicker em {
  padding: 3px 6px;
  border-radius: 999px;
  color: var(--plaza-accent);
  background: var(--plaza-accent-soft);
  font-family: inherit;
  font-size: 7px;
  font-style: normal;
}
.step-title h3 {
  margin: 5px 0 0;
  color: var(--plaza-heading);
  font-family: var(--font-display);
  font-size: 15px;
  font-weight: 750;
  line-height: 1.35;
}
.step-status {
  display: inline-flex;
  min-height: 24px;
  flex: 0 0 auto;
  align-items: center;
  gap: 5px;
  padding: 0 8px;
  border-radius: 999px;
  font-size: 9px;
  font-weight: 800;
}

.step-readout {
  display: flex;
  min-height: 38px;
  align-items: center;
  flex-wrap: wrap;
  gap: 7px 13px;
  padding: 0 15px 11px;
  color: var(--plaza-text-muted);
  font-size: 9px;
}
.step-readout > span { display: inline-flex; align-items: center; gap: 5px; }
.step-readout > span .el-icon { color: var(--plaza-accent); font-size: 12px; }
.step-tools { display: flex; align-items: center; gap: 6px; margin-left: auto; }

.ask-button,
.detail-toggle {
  display: inline-flex;
  min-height: 30px;
  align-items: center;
  justify-content: center;
  gap: 5px;
  padding: 0 9px;
  border-radius: 7px;
  background: var(--plaza-bg-card);
  font-size: 9px;
  font-weight: 750;
  cursor: pointer;
}
.ask-button { border: 1px solid var(--plaza-accent-soft-strong); color: var(--plaza-accent); }
.detail-toggle { border: 1px solid var(--plaza-border); color: var(--plaza-text-muted); }
.ask-button:hover,
.detail-toggle:hover { border-color: var(--plaza-accent); color: var(--plaza-accent); background: var(--plaza-accent-soft); }
.ask-button.reading { border-color: var(--plaza-accent); color: #fff; background: var(--plaza-accent); }
.rollback-btn { border-color: rgba(197, 64, 44, 0.3); color: #c5402c; }
.rollback-btn:hover { border-color: #c5402c; color: #fff; background: #c5402c; }

.step-body { padding: 14px 15px 15px; border-top: 1px solid var(--plaza-border); }
.step-content { margin: 0 0 11px; color: var(--plaza-text); font-size: 12px; line-height: 1.75; white-space: pre-wrap; }

.safety-note {
  display: grid;
  grid-template-columns: 108px minmax(0, 1fr);
  gap: 12px;
  margin-bottom: 10px;
  padding: 10px 11px;
  border: 1px solid #f3d3a0;
  border-radius: 9px;
  background: var(--plaza-warning-soft);
}
.safety-note > span { display: flex; align-items: center; gap: 6px; color: #b45309; font-size: 10px; font-weight: 800; }
.safety-note p { margin: 0; color: #8a6f4a; font-size: 10px; line-height: 1.6; }

.checkpoint {
  margin-bottom: 10px;
  padding: 11px;
  border: 1px solid var(--plaza-border);
  border-radius: 9px;
  background: var(--plaza-bg-input);
}
.checkpoint-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 7px; }
.checkpoint-head span { color: var(--plaza-text-muted); font-family: var(--font-mono); font-size: 8px; font-weight: 800; letter-spacing: 0.11em; }
.checkpoint-head b { color: var(--plaza-text-muted); font-size: 9px; }
.checkpoint ul { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; margin: 0; padding: 0; list-style: none; }
.checkpoint li { display: flex; min-width: 0; align-items: flex-start; gap: 7px; color: var(--plaza-text); font-size: 10px; line-height: 1.55; }
.checkpoint li i { flex: 0 0 auto; color: var(--plaza-accent); font-family: var(--font-mono); font-size: 8px; font-style: normal; font-weight: 800; }

.submitted-images { display: flex; flex-wrap: wrap; gap: 6px; margin: 10px 0; }
.submitted-images img { width: 62px; height: 62px; object-fit: cover; border: 1px solid var(--plaza-border); border-radius: 7px; }
.submitted-note { margin: 9px 0 0; padding: 9px 10px; border-radius: 8px; color: var(--plaza-text); background: var(--plaza-bg-input); font-size: 10px; line-height: 1.6; }
.submitted-note b { margin-right: 8px; color: var(--plaza-heading); }

.ai-verdict {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr);
  gap: 9px;
  margin-top: 10px;
  padding: 10px;
  border: 1px solid;
  border-radius: 9px;
}
.ai-verdict.passed { color: #4e7a32; border-color: #cbe0ad; background: var(--plaza-success-soft); }
.ai-verdict.failed { color: #b45309; border-color: #f3d3a0; background: var(--plaza-warning-soft); }
.verdict-icon { display: grid; width: 34px; height: 34px; place-items: center; border-radius: 8px; background: currentColor; }
.verdict-icon .el-icon { color: #fff; }
.verdict-copy { display: flex; min-width: 0; flex-direction: column; }
.ai-verdict b { display: inline-flex; align-items: center; flex-wrap: wrap; gap: 8px; font-size: 11px; }
.ai-verdict .conf { padding: 1px 8px; border-radius: 10px; font-style: normal; font-weight: 700; font-size: 9px; }
.ai-verdict .conf-high { background: #e2f3d6; color: #4e7a32; }
.ai-verdict .conf-mid { background: #fdf0d5; color: #b45309; }
.ai-verdict .conf-low { background: #fbe0e0; color: #c0392b; }
.ai-verdict p { margin: 5px 0 0; color: var(--plaza-text); font-size: 10px; line-height: 1.55; }

.execution-panel {
  margin-top: 12px;
  padding: 13px;
  border: 1px solid var(--plaza-accent-soft-strong);
  border-radius: 10px;
  background:
    linear-gradient(var(--plaza-accent-soft) 1px, transparent 1px),
    linear-gradient(90deg, var(--plaza-accent-soft) 1px, transparent 1px),
    var(--plaza-bg-input);
  background-size: 22px 22px;
}
.execution-heading { display: flex; align-items: flex-end; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
.execution-heading span { color: var(--plaza-text-muted); font-family: var(--font-mono); font-size: 7px; font-weight: 800; letter-spacing: 0.12em; }
.execution-heading h4 { margin: 3px 0 0; color: var(--plaza-heading); font-size: 12px; }
.execution-heading small { color: var(--plaza-text-muted); font-size: 8px; }
.execution-grid { display: grid; grid-template-columns: minmax(180px, 0.42fr) minmax(220px, 1fr); gap: 10px; }

.upload-zone { display: flex; min-height: 82px; align-content: flex-start; flex-wrap: wrap; gap: 7px; }
.upload-preview,
.upload-button { position: relative; width: 74px; height: 74px; overflow: hidden; border-radius: 8px; }
.upload-preview { border: 1px solid var(--plaza-border-strong); }
.upload-preview img { width: 100%; height: 100%; object-fit: cover; }
.upload-preview button { position: absolute; top: 3px; right: 3px; display: grid; width: 20px; height: 20px; place-items: center; border: 0; border-radius: 50%; color: #fff; background: rgba(0, 0, 0, 0.72); cursor: pointer; }
.upload-button { display: flex; align-items: center; justify-content: center; flex-direction: column; gap: 4px; border: 1px dashed var(--plaza-border-strong); color: var(--plaza-accent); background: rgba(255, 255, 255, 0.6); font-size: 9px; font-weight: 750; cursor: pointer; }
.upload-button .el-icon { font-size: 19px; }
.upload-button:hover { background: var(--plaza-accent-soft); }
.upload-button.busy { opacity: 0.6; pointer-events: none; }

.execution-grid :deep(.el-textarea__inner) {
  min-height: 82px !important;
  border-radius: 8px;
  color: var(--plaza-text);
  background: rgba(255, 255, 255, 0.82);
  box-shadow: 0 0 0 1px var(--plaza-border-strong) inset;
  font-size: 11px;
  line-height: 1.6;
}
.execution-grid :deep(.el-textarea__inner:focus) { box-shadow: 0 0 0 1px var(--plaza-accent) inset, 0 0 0 3px var(--plaza-accent-soft); }

.checkpoint-confirm { display: flex; align-items: flex-start; gap: 9px; margin-top: 10px; padding: 9px 10px; border: 1px solid var(--plaza-border-strong); border-radius: 8px; background: rgba(255, 255, 255, 0.7); cursor: pointer; }
.checkpoint-confirm input { width: 16px; height: 16px; margin: 2px 0 0; accent-color: var(--plaza-accent); }
.checkpoint-confirm span { display: flex; min-width: 0; flex-direction: column; }
.checkpoint-confirm b { color: var(--plaza-heading); font-size: 10px; }
.checkpoint-confirm small { margin-top: 2px; color: var(--plaza-text-muted); font-size: 8px; }

.execution-actions { display: flex; align-items: center; justify-content: flex-end; gap: 8px; margin-top: 11px; }
.execution-actions > span { margin-right: auto; color: var(--plaza-text-muted); font-size: 8px; }
.submit-button,
.force-button { min-height: 39px; padding: 0 15px; border-radius: 8px; font-size: 10px; font-weight: 800; cursor: pointer; }
.submit-button { border: 1px solid transparent; color: #fff; background: var(--plaza-accent-grad); box-shadow: 0 7px 18px var(--plaza-accent-soft-strong); }
.submit-button:hover { filter: brightness(1.05); }
.force-button { border: 1px solid #f3d3a0; color: var(--plaza-warning); background: var(--plaza-bg-card); }
.submit-button:disabled,
.force-button:disabled { opacity: 0.55; cursor: not-allowed; }

@media (max-width: 680px) {
  .step-card-shell { grid-template-columns: 44px minmax(0, 1fr); }
  .timeline-node { width: 30px; height: 30px; }
  .step-header { align-items: flex-start; flex-direction: column; }
  .step-status { align-self: flex-start; }
  .step-tools { width: 100%; margin-left: 0; }
  .safety-note,
  .execution-grid { grid-template-columns: 1fr; }
  .checkpoint ul { grid-template-columns: 1fr; }
  .execution-heading { align-items: flex-start; flex-direction: column; }
  .execution-actions { align-items: stretch; flex-direction: column; }
  .execution-actions > span { margin-right: 0; }
}
@media (prefers-reduced-motion: reduce) {
  .node-spinner,
  .status-spinner { animation: none; }
  .step-card { transition: none; }
}
</style>
