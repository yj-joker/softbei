// 检修任务 / 步骤 状态枚举 → 中文文案 + 主题语义色（矿石白 + 克制蓝）。
// 统一在此映射，避免散落硬编码。

/** 任务状态：CREATED → GENERATING → GENERATED → EXECUTING → CLOSED，异常分支 GENERATE_FAILED */
export const TASK_STATUS = {
  CREATED:         { label: '待生成',  color: '#8a7c6c', bg: '#f1eadd' },
  GENERATING:      { label: '生成中',  color: '#c4602f', bg: '#f8ece2', spin: true },
  GENERATED:       { label: '待执行',  color: '#c4602f', bg: '#f8ece2' },
  EXECUTING:       { label: '执行中',  color: '#c4602f', bg: '#f8ece2' },
  CLOSED:          { label: '已完成',  color: '#5e8c3e', bg: '#f1f5e6' },
  GENERATE_FAILED: { label: '生成失败', color: '#c5402c', bg: '#fbeae4' },
}
export const taskStatus = (s) => TASK_STATUS[s] || { label: s || '未知', color: '#8a7c6c', bg: '#f1eadd' }

/** 步骤状态：PENDING → SUBMITTED →(AI_PASSED | AI_REJECTED)→ COMPLETED，可 SKIPPED */
const STEP_STATUS = {
  PENDING:     { label: '待执行',   color: '#8a7c6c', bg: '#f1eadd' },
  SUBMITTED:   { label: 'AI验证中', color: '#c4602f', bg: '#f8ece2', spin: true },
  AI_PASSED:   { label: 'AI通过',   color: '#5e8c3e', bg: '#f1f5e6' },
  AI_REJECTED: { label: 'AI未通过', color: '#df9226', bg: '#fdf2e0' },
  COMPLETED:   { label: '已完成',   color: '#5e8c3e', bg: '#f1f5e6' },
  SKIPPED:     { label: '已跳过',   color: '#b3a692', bg: '#f1eadd' },
}
export const stepStatus = (s) => STEP_STATUS[s] || { label: s || '未知', color: '#8a7c6c', bg: '#f1eadd' }
/** 步骤是否处于「已了结」（绿/灰，不可再执行） */
/** 步骤是否可由工人执行/重做 */
export const stepActionable = (s) => ['PENDING', 'AI_REJECTED'].includes(s)

/** 紧急等级：0 低 / 1 普通 / 2 紧急 */
export const URGENCY = [
  { value: 0, label: '低',   color: '#5e8c3e', bg: '#f1f5e6' },
  { value: 1, label: '普通', color: '#c4602f', bg: '#f8ece2' },
  { value: 2, label: '紧急', color: '#c5402c', bg: '#fbeae4' },
]
export const urgency = (v) => URGENCY.find((u) => u.value === v) || URGENCY[1]

/** 检修等级 */
export const MAINTENANCE_LEVEL = [
  { value: 'ROUTINE', label: '日常保养' },
  { value: 'MINOR',   label: '小修' },
  { value: 'MAJOR',   label: '大修' },
]
export const levelLabel = (v) => (MAINTENANCE_LEVEL.find((l) => l.value === v) || {}).label || v || '—'
