<script setup>
import { ref, reactive } from 'vue'
import { ElMessage } from 'element-plus'
import { createTask } from '@/api/maintenanceTask'
import { searchDevices } from '@/api/graph'
import { uploadImage } from '@/api/user'
import { notifyStore } from '@/stores/notifyStore'
import { URGENCY, MAINTENANCE_LEVEL } from '@/constants/taskStatus'

const props = defineProps({ modelValue: { type: Boolean, default: false } })
const emit = defineEmits(['update:modelValue', 'created'])

const form = reactive({
  deviceId: '', deviceName: '', faultDescription: '',
  urgencyLevel: 1, maintenanceLevel: '', aiAdapt: true,
})
const reportImages = ref([])      // 已上传的图片 URL
const uploading = ref(false)
const submitting = ref(false)

// 设备远程搜索
const deviceOptions = ref([])
const deviceLoading = ref(false)
async function onSearchDevice(kw) {
  deviceLoading.value = true
  try {
    const res = await searchDevices((kw || '').trim(), 20)
    deviceOptions.value = res?.data || []
  } catch { deviceOptions.value = [] } finally { deviceLoading.value = false }
}
function onDevicePick(id) {
  const d = deviceOptions.value.find((x) => x.id === id)
  form.deviceName = d ? d.name : ''
}

async function onPickFiles(e) {
  const files = Array.from(e.target.files || [])
  e.target.value = ''
  if (!files.length) return
  uploading.value = true
  try {
    for (const f of files) {
      const res = await uploadImage(f)
      const url = res?.data || res?.url
      if (url) reportImages.value.push(url)
    }
  } catch (err) {
    ElMessage.error('图片上传失败：' + (err.message || ''))
  } finally { uploading.value = false }
}
function removeImage(i) { reportImages.value.splice(i, 1) }

function close() { emit('update:modelValue', false) }
function resetForm() {
  Object.assign(form, { deviceId: '', deviceName: '', faultDescription: '', urgencyLevel: 1, maintenanceLevel: '', aiAdapt: true })
  reportImages.value = []
}

async function submit() {
  if (!form.faultDescription.trim()) { ElMessage.warning('请填写故障描述'); return }
  submitting.value = true
  try {
    const payload = {
      deviceId: form.deviceId || null,
      deviceName: form.deviceName || null,
      faultDescription: form.faultDescription.trim(),
      urgencyLevel: form.urgencyLevel,
      maintenanceLevel: form.maintenanceLevel || null,
      reportImages: reportImages.value,
      aiAdapt: form.aiAdapt,
    }
    const res = await createTask(payload)
    const task = res?.data || {}
    const taskId = task.id
    // 异步生成步骤 → 进行中托盘 + 完成 Toast（WS TASK_GENERATED 自动清理）
    if (taskId) {
      notifyStore.trackJob({
        key: 'task:' + taskId, kind: 'task', refId: taskId,
        title: '生成检修步骤' + (form.deviceName ? '：' + form.deviceName : ''),
      })
    }
    ElMessage.success('任务已创建，正在生成检修步骤')
    emit('created', taskId)
    close()
    resetForm()
  } catch (err) {
    ElMessage.error('创建失败：' + (err.message || ''))
  } finally { submitting.value = false }
}
</script>

<template>
  <el-dialog :model-value="modelValue" title="新建检修任务" width="540px" align-center append-to-body
             @update:model-value="emit('update:modelValue', $event)" @closed="resetForm">
    <el-form label-width="84px" @submit.prevent>
      <el-form-item label="设备">
        <el-select v-model="form.deviceId" filterable remote clearable :remote-method="onSearchDevice"
                   :loading="deviceLoading" placeholder="搜索设备名 / 编码（可不选）" style="width:100%"
                   @change="onDevicePick" @focus="onSearchDevice('')">
          <el-option v-for="d in deviceOptions" :key="d.id" :label="d.name" :value="d.id" />
        </el-select>
      </el-form-item>

      <el-form-item label="故障描述" required>
        <el-input v-model="form.faultDescription" type="textarea" :rows="3"
                  placeholder="描述故障现象，例如：发动机怠速抖动、伴随异响…" />
      </el-form-item>

      <el-form-item label="紧急等级">
        <el-radio-group v-model="form.urgencyLevel">
          <el-radio-button v-for="u in URGENCY" :key="u.value" :value="u.value">{{ u.label }}</el-radio-button>
        </el-radio-group>
      </el-form-item>

      <el-form-item label="检修等级">
        <el-select v-model="form.maintenanceLevel" clearable placeholder="可不选（由AI判断）" style="width:100%">
          <el-option v-for="l in MAINTENANCE_LEVEL" :key="l.value" :label="l.label" :value="l.value" />
        </el-select>
      </el-form-item>

      <el-form-item label="AI微调">
        <el-switch v-model="form.aiAdapt" />
        <span class="hint">匹配到标准规程时，AI 按本次故障对步骤做针对性调整</span>
      </el-form-item>

      <el-form-item label="报修图片">
        <div class="imgs">
          <div v-for="(u, i) in reportImages" :key="i" class="img-item">
            <img :src="u" alt="" />
            <button class="img-x" @click="removeImage(i)">移除</button>
          </div>
          <label class="img-add" :class="{ busy: uploading }">
            <input type="file" accept="image/*" multiple hidden @change="onPickFiles" />
            <span v-if="uploading">上传中</span><span v-else>添加图片</span>
          </label>
        </div>
      </el-form-item>
    </el-form>

    <template #footer>
      <button class="dlg-btn cancel" @click="close">取消</button>
      <button class="dlg-btn ok" :disabled="submitting" @click="submit">{{ submitting ? '创建中…' : '创建任务' }}</button>
    </template>
  </el-dialog>
</template>

<style scoped>
.hint { font-size: 12px; color: var(--plaza-text-muted); margin-left: 10px; }
.imgs { display: flex; flex-wrap: wrap; gap: 10px; }
.img-item { position: relative; width: 72px; height: 72px; border-radius: 8px; overflow: hidden; border: 1px solid var(--plaza-border); }
.img-item img { width: 100%; height: 100%; object-fit: cover; }
.img-x { position: absolute; top: 2px; right: 2px; width: 18px; height: 18px; border: none; border-radius: 50%;
  background: rgba(25,18,13,.6); color: #fff; font-size: 11px; cursor: pointer; line-height: 1; }
.img-add { width: 72px; height: 72px; border: 1px dashed var(--plaza-border); border-radius: 8px; display: flex; align-items: center;
  justify-content: center; font-size: 13px; color: var(--plaza-accent); cursor: pointer; background: var(--plaza-bg-card); }
.img-add.busy { color: var(--plaza-text-muted); cursor: default; }
.img-add:hover { background: var(--plaza-panel-bg); }
.dlg-btn { padding: 8px 18px; border-radius: 8px; font-weight: 600; font-size: 13px; cursor: pointer; border: 1px solid; transition: .15s; }
.dlg-btn.cancel { background: var(--plaza-bg-card); color: var(--plaza-text); border-color: var(--plaza-border); margin-right: 8px; }
.dlg-btn.cancel:hover { color: var(--plaza-text); }
.dlg-btn.ok { background: var(--plaza-accent-grad); color: #fff; border-color: var(--plaza-accent); }
.dlg-btn.ok:hover { background: var(--plaza-accent-hover); }
.dlg-btn.ok:disabled { opacity: .6; cursor: not-allowed; }
</style>
