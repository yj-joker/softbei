import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const apiPath = path.join(root, 'src/api/taskEvidenceCandidate.js')
const adminPath = path.join(root, 'src/views/adminViews/AdminTasks.vue')
const panelPath = path.join(root, 'src/components/DistillationReviewPanel.vue')
const reviewPath = path.join(root, 'src/components/TaskEvidenceCandidateReview.vue')
const editorPath = path.join(root, 'src/components/task-evidence/TaskEvidenceGraphEditor.vue')
const supportPath = path.join(root, 'src/components/task-evidence/TaskEvidenceSupportPanel.vue')

const read = (file) => fs.existsSync(file) ? fs.readFileSync(file, 'utf8') : ''
const apiSource = read(apiPath)
const reviewSource = read(reviewPath)
const sourceFunction = (source, name, nextName) => {
  const start = source.indexOf(`function ${name}`)
  const asyncStart = source.indexOf(`async function ${name}`)
  const actualStart = asyncStart >= 0 && (start < 0 || asyncStart < start) ? asyncStart : start
  const markers = nextName ? [source.indexOf(`function ${nextName}`, actualStart + 1), source.indexOf(`async function ${nextName}`, actualStart + 1)].filter((index) => index >= 0) : []
  const end = markers.length ? Math.min(...markers) : source.length
  return actualStart >= 0 ? source.slice(actualStart, end) : ''
}
const functionSource = (name, nextName) => sourceFunction(apiSource, name, nextName)
const reviewFunctionSource = (name, nextName) => sourceFunction(reviewSource, name, nextName)
const checks = [
  ['候选 API 文件存在', fs.existsSync(apiPath)],
  ['API 使用分页路径', /\/weixiu\/admin\/task-evidence-candidates/.test(read(apiPath))],
  ['API 包含详情/重试/审核路径', /\$\{id\}\/retry/.test(read(apiPath)) && /\$\{id\}\/review/.test(read(apiPath)) && /\$\{id\}`/.test(read(apiPath))],
  ['审核 API 传递 status 与 rowVersion', /status/.test(read(apiPath)) && /rowVersion/.test(read(apiPath))],
  ['AdminTasks 接入候选审核 Tab', /TaskEvidenceCandidateReview|候选审核/.test(read(adminPath))],
  ['候选审核组件存在', fs.existsSync(reviewPath)],
  ['候选审核组件包含重试与审核文案', /重试/.test(read(reviewPath)) && /通过|驳回/.test(read(reviewPath))],
  ['整理与审核状态使用独立文案映射', /const extractionStatusLabel\s*=\s*\{[^}]*PENDING:\s*['"]候选整理中['"][^}]*READY:\s*['"]待审核['"]/s.test(read(reviewPath)) && /const reviewStatusLabel\s*=\s*\{[^}]*PENDING:\s*['"]待审核['"][^}]*APPROVED:\s*['"]已通过['"]/s.test(read(reviewPath)) && /extractionStatusLabel\[row\.extractionStatus\]/.test(read(reviewPath)) && /reviewStatusLabel\[row\.reviewStatus\]/.test(read(reviewPath))],
  ['候选详情改为行内单项展开', /const expandedCandidateId\s*=\s*ref\(null\)/.test(read(reviewPath)) && /class="tec-inline-detail"/.test(read(reviewPath)) && !/<el-drawer\b/.test(read(reviewPath))],
  ['点击查看先通过守卫再加载详情', /async function openDetail\(row\)\s*\{[\s\S]*?await guardDirtyDraft\([\s\S]*?expandedCandidateId\.value\s*=\s*id[\s\S]*?selected\.value\s*=\s*\{ \.\.\.row, id \}[\s\S]*?detailLoading\.value\s*=\s*true[\s\S]*?await getTaskEvidenceCandidate/.test(read(reviewPath))],
  ['父组件接入图谱编辑器与支持证据面板', /import TaskEvidenceGraphEditor from/.test(read(reviewPath)) && /import TaskEvidenceSupportPanel from/.test(read(reviewPath)) && /<TaskEvidenceGraphEditor\b/.test(read(reviewPath)) && /<TaskEvidenceSupportPanel\b/.test(read(reviewPath)) && fs.existsSync(editorPath) && fs.existsSync(supportPath)],
  ['候选更新 API 使用 PUT 与业务错误抛出', /export function updateTaskEvidenceCandidate\(id, data\)/.test(apiSource) && /url:\s*`\$\{BASE\}\/\$\{String\(id\)\}`/.test(functionSource('updateTaskEvidenceCandidate')) && /method:\s*['"]PUT['"]/.test(functionSource('updateTaskEvidenceCandidate')) && /data/.test(functionSource('updateTaskEvidenceCandidate')) && /throwOnError:\s*true/.test(functionSource('updateTaskEvidenceCandidate'))],
  ['详情草稿和基线使用深层规范化副本', /normalizeCandidateGraph/.test(read(reviewPath)) && /const draft\s*=\s*ref/.test(read(reviewPath)) && /const baseline\s*=\s*ref/.test(read(reviewPath)) && /function initializeDraft/.test(read(reviewPath)) && /draft\.value\s*=\s*normalizeCandidateGraph/.test(read(reviewPath)) && /baseline\.value\s*=\s*normalizeCandidateGraph/.test(read(reviewPath))],
  ['编辑器变更建立 dirty 状态', /const dirty\s*=\s*ref\(false\)/.test(read(reviewPath)) && /@dirty="markDirty"/.test(read(reviewPath)) && /function markDirty/.test(read(reviewPath))],
  ['保存与放弃操作可见', /保存草稿/.test(read(reviewPath)) && /放弃修改/.test(read(reviewPath)) && /function saveDraft/.test(read(reviewPath)) && /function discardDraft/.test(read(reviewPath))],
  ['保存请求携带 rowVersion candidateJson editComment 且直接传对象', /const candidateJson\s*=\s*normalizeCandidateGraph\(draft\.value\)/.test(reviewFunctionSource('saveDraft', 'guardDirtyDraft')) && /updateTaskEvidenceCandidate\([\s\S]*rowVersion,[\s\S]*candidateJson,[\s\S]*editComment:/.test(reviewFunctionSource('saveDraft', 'guardDirtyDraft')) && !/candidateJson:\s*JSON\.stringify\(draft\.value\)/.test(read(reviewPath))],
  ['保存响应受候选 ID 和操作 token 保护', /const token\s*=\s*\+\+operationToken/.test(reviewFunctionSource('saveDraft', 'guardDirtyDraft')) && /token === operationToken/.test(reviewFunctionSource('saveDraft', 'guardDirtyDraft')) && /expandedCandidateId\.value === id/.test(reviewFunctionSource('saveDraft', 'guardDirtyDraft')) && /candidateId\(selected\.value\) === id/.test(reviewFunctionSource('saveDraft', 'guardDirtyDraft'))],
  ['保存响应 VO ID 必须匹配请求候选', /candidateId\(vo\)\s*!==\s*id/.test(reviewFunctionSource('saveDraft', 'guardDirtyDraft')) && /响应候选不匹配/.test(reviewFunctionSource('saveDraft', 'guardDirtyDraft'))],
  ['保存成功用 VO 同步详情列表版本和基线', /function applyCandidateVo/.test(read(reviewPath)) && /selected\.value\s*=/.test(read(reviewPath)) && /rows\.value\s*=\s*rows\.value\.map/.test(read(reviewPath)) && /initializeDraft\(vo\)/.test(read(reviewPath)) && /rowVersion/.test(read(reviewPath))],
  ['保存失败保留草稿并显示错误', /catch \(error\) \{[\s\S]*保存失败/.test(reviewFunctionSource('saveDraft', 'guardDirtyDraft')) && !/initializeDraft/.test(reviewFunctionSource('saveDraft', 'guardDirtyDraft'))],
  ['保存审核入图重试操作均有互斥守卫', /if \(.*saving\.value.*busyIds\.value\.size/.test(reviewFunctionSource('saveDraft', 'guardDirtyDraft')) && /if \(saving\.value \|\| busyIds\.value\.size/.test(reviewFunctionSource('review', 'promote')) && /if \(saving\.value \|\| busyIds\.value\.size/.test(reviewFunctionSource('promote', 'viewTaskGraph')) && /if \(saving\.value \|\| busyIds\.value\.size/.test(reviewFunctionSource('retry', 'review'))],
  ['任意 dirty 草稿禁止行内与详情重试', /if \(saving\.value \|\| busyIds\.value\.size \|\| dirty\.value\) return/.test(reviewFunctionSource('retry', 'review')) && /重新整理[^<]*<\/el-button>|重新整理/.test(read(reviewPath)) && /v-if="row\.extractionStatus === 'FAILED'"[^>]*:disabled="anyOperationBusy \|\| dirty"/.test(read(reviewPath)) && /v-if="selectedCanRetry"[^>]*:disabled="anyOperationBusy \|\| dirty"/.test(read(reviewPath))],
  ['审核和入图期间编辑器只读', /const anyOperationBusy\s*=\s*computed\([\s\S]*saving\.value[\s\S]*busyIds\.value\.size/.test(read(reviewPath)) && /promotedGraph\s*===\s*['"]PROMOTED['"] \|\| anyOperationBusy\.value/.test(read(reviewPath)) && /:read-only="editorReadOnly"/.test(read(reviewPath))],
  ['放弃恢复规范化基线', /function discardDraft\(\)[\s\S]*draft\.value\s*=\s*normalizeCandidateGraph\(baseline\.value\)[\s\S]*dirty\.value\s*=\s*false/.test(read(reviewPath))],
  ['详情与重开详情响应 ID 必须匹配请求候选', /candidateId\(res\.data\)\s*!==\s*id/.test(reviewFunctionSource('openDetail', 'reopenDetail')) && /详情响应候选不匹配/.test(reviewFunctionSource('openDetail', 'reopenDetail')) && /candidateId\(res\.data\)\s*!==\s*id/.test(reviewFunctionSource('reopenDetail', 'markBusy')) && /详情响应候选不匹配/.test(reviewFunctionSource('reopenDetail', 'markBusy'))],
  ['切换候选和收起详情使用全局 busy 与未保存守卫', /async function guardDirtyDraft/.test(read(reviewPath)) && /ElMessageBox\.confirm\([\s\S]*未保存/.test(read(reviewPath)) && /anyOperationBusy\.value/.test(reviewFunctionSource('closeDetail', 'stopPolling')) && /anyOperationBusy\.value/.test(reviewFunctionSource('openDetail', 'reopenDetail')) && /await guardDirtyDraft\(/.test(reviewFunctionSource('closeDetail', 'stopPolling')) && /await guardDirtyDraft\(/.test(reviewFunctionSource('openDetail', 'reopenDetail'))],
  ['清理详情同步结束详情 loading', /function clearDetail\(\)[\s\S]*detailLoading\.value\s*=\s*false/.test(read(reviewPath))],
  ['脏状态禁止审核并提示先保存', /selectedCanReview[\s\S]*!dirty\.value/.test(read(reviewPath)) && /请先保存/.test(read(reviewPath)) && /:disabled="!selectedCanReview"/.test(read(reviewPath))],
  ['操作后通过内部强制路径真实刷新列表', /async function load\(pageNumber = 1, polling = false, internal = false\)/.test(read(reviewPath)) && /if \(!polling && !internal && anyOperationBusy\.value\) return/.test(read(reviewPath)) && /await load\(page\.page, false, true\)/.test(reviewFunctionSource('retry', 'review')) && /await load\(page\.page, false, true\)/.test(reviewFunctionSource('review', 'promote')) && /await load\(page\.page, false, true\)/.test(reviewFunctionSource('promote', 'viewTaskGraph'))],
  ['前台与内部刷新不会被轮询抢占 loading', /let foregroundLoadToken\s*=\s*0/.test(read(reviewPath)) && /if \(polling && \(foregroundLoadToken \|\| anyOperationBusy\.value\)\) return/.test(reviewFunctionSource('load', 'reset')) && /const foregroundToken\s*=\s*!polling \? \+\+foregroundLoadToken : 0/.test(reviewFunctionSource('load', 'reset')) && /if \(foregroundToken === foregroundLoadToken\) \{[\s\S]*loading\.value = false[\s\S]*foregroundLoadToken = 0/.test(reviewFunctionSource('load', 'reset')) && /stopPolling\(\)/.test(reviewFunctionSource('retry', 'review')) && /stopPolling\(\)/.test(reviewFunctionSource('review', 'promote')) && /stopPolling\(\)/.test(reviewFunctionSource('promote', 'viewTaskGraph'))],
  ['业务操作结束后在解除 busy 后恢复轮询', /saving\.value = false[\s\S]*syncPolling\(\)/.test(reviewFunctionSource('saveDraft', 'guardDirtyDraft')) && /markBusy\(id, false\)[\s\S]*syncPolling\(\)/.test(reviewFunctionSource('retry', 'review')) && /markBusy\(id, false\)[\s\S]*syncPolling\(\)/.test(reviewFunctionSource('review', 'promote')) && /markBusy\(id, false\)[\s\S]*syncPolling\(\)/.test(reviewFunctionSource('promote', 'viewTaskGraph'))],
  ['审核 prompt 与入图 confirm 取消后仍恢复轮询', /catch \{[\s\S]*return[\s\S]*\}[\s\S]*finally \{[\s\S]*markBusy\(id, false\)[\s\S]*syncPolling\(\)/.test(reviewFunctionSource('review', 'promote')) && /catch \{[\s\S]*return[\s\S]*\}[\s\S]*finally \{[\s\S]*markBusy\(id, false\)[\s\S]*syncPolling\(\)/.test(reviewFunctionSource('promote', 'viewTaskGraph'))],
  ['筛选分页和行点击路径在操作期间禁用', (read(reviewPath).match(/:disabled="anyOperationBusy"/g) || []).length >= 9 && /<el-pagination[^>]*:disabled="anyOperationBusy"/.test(read(reviewPath)) && /@row-click="openDetail"/.test(read(reviewPath)) && /if \(anyOperationBusy\.value\) return/.test(reviewFunctionSource('openDetail', 'reopenDetail'))],
  ['入图后编辑器只读', /const editorReadOnly\s*=\s*computed\([\s\S]*promotedGraph\s*===\s*['"]PROMOTED['"]/.test(read(reviewPath)) && /:read-only="editorReadOnly"/.test(read(reviewPath))],
  ['轮询仅刷新列表不覆盖详情草稿', /async function load\(pageNumber = 1, polling = false(?:, internal = false)?\)/.test(read(reviewPath)) && !/if \(polling\)[\s\S]{0,240}(selected|draft)\.value\s*=/.test(read(reviewPath))],
  ['旧只读森林和证据 CSS 已移除', !/function candidateForest\(/.test(read(reviewPath)) && !/function groupedEvidence\(/.test(read(reviewPath)) && !/\.tec-tree\s*\{/.test(read(reviewPath)) && !/\.tec-evidence-field\s*\{/.test(read(reviewPath))],
  ['候选列表和详情业务错误会抛出', /throwOnError:\s*true/.test(functionSource('getTaskEvidenceCandidateList', 'getTaskEvidenceCandidate')) && /throwOnError:\s*true/.test(functionSource('getTaskEvidenceCandidate', 'retryTaskEvidenceCandidate'))],
  ['候选提供显式入图 API', /export function promoteTaskEvidenceCandidate\(id\)/.test(apiSource) && /\$\{BASE\}\/\$\{id\}\/promote/.test(apiSource) && /throwOnError:\s*true/.test(functionSource('promoteTaskEvidenceCandidate'))],
  ['审核通过后提供入图与图谱查看操作', /selectedCanPromote/.test(read(reviewPath)) && /沉淀到知识图谱/.test(read(reviewPath)) && /查看本次图谱/.test(read(reviewPath)) && /promoteTaskEvidenceCandidate/.test(read(reviewPath))],
  ['查看图谱具有 handler 与按钮 busy 双层守卫', /if \(anyOperationBusy\.value\) return/.test(reviewFunctionSource('viewTaskGraph', 'onMounted')) && /v-if="selectedCanViewGraph"[^>]*:disabled="anyOperationBusy"[^>]*@click="viewTaskGraph/.test(read(reviewPath))],
  ['规范字段 evidence 由图谱工具消费', /source\.evidence/.test(read(path.join(root, 'src/utils/taskEvidenceCandidateGraph.js')))],
  ['支持面板读取规范 evidenceJson', /candidate\?\.evidenceJson/.test(read(supportPath))],
  ['实体关系详情交由编辑器并展示支持证据', /<TaskEvidenceGraphEditor\b/.test(read(reviewPath)) && /<TaskEvidenceSupportPanel\b/.test(read(reviewPath))],
  ['FAILED 显示真实错误字段', /extractionError[\s\S]*error[\s\S]*reviewComment/.test(read(reviewPath))],
  ['resolution 包含 PARTIALLY_RESOLVED', /PARTIALLY_RESOLVED/.test(read(reviewPath))],
  ['AdminTasks 包含 RESOLUTION_PENDING', /RESOLUTION_PENDING/.test(read(adminPath))],
  ['规程查询只使用 CLOSED 与 promotedProcedure PENDING', /status:\s*'CLOSED',\s*promotedProcedure:\s*'PENDING'/.test(read(panelPath)) && !/promotedGraph:\s*'PENDING'/.test(read(panelPath))],
  ['AdminTasks 规程判定不读取 promotedGraph', /function needsDistill[\s\S]{0,160}promotedProcedure === 'PENDING'/.test(read(adminPath)) && !/needsDistill[\s\S]{0,180}promotedGraph/.test(read(adminPath))],
  ['AdminTasks 跳过只处理 procedure', /handleSkip\(row, 'procedure'\)/.test(read(adminPath)) && !/handleSkip\(row, 'both'\)/.test(read(adminPath))],
  ['候选 extractionError VO 跨层', /extractionError/.test(read(reviewPath)) && /extractionError/.test(read(path.join(root, '..', 'weixiu/src/main/java/ai/weixiu/pojo/vo/TaskEvidenceCandidateVO.java')))],
  ['evidence 字符串在支持面板先 parse', /evidenceJson\s*\?\s*parse\(props\.candidate\.evidenceJson\)/.test(read(supportPath))],
  ['候选审核组件删除两处冗余说明', !/<header[^>]*class="tec-head"[\s\S]*?<p>[\s\S]*?<\/p>[\s\S]*?<\/header>/.test(read(reviewPath)) && !/class="tec-note"/.test(read(reviewPath))],
  ['PENDING 候选自动轮询并按状态停止', /function syncPolling\(\)[\s\S]*rows\.value\.some\(\(row\)\s*=>\s*row\.extractionStatus\s*===\s*['"]PENDING['"]\)[\s\S]*setTimeout\(\(\)\s*=>\s*load\(page\.page,\s*true\),\s*\d+\)/.test(read(reviewPath)) && /function stopPolling\(\)[\s\S]*clearTimeout\(pollTimer\)/.test(read(reviewPath))],
  ['组件卸载时清理候选轮询', /onUnmounted\(\(\)\s*=>\s*stopPolling\(\)\)/.test(read(reviewPath))],
  ['候选筛选控件桌面受限宽度且移动端纵排', /\.tec-filter-control\s*\{[^}]*flex:\s*0\s+1\s+\d+px[^}]*width:\s*\d+px[^}]*\}/s.test(read(reviewPath)) && /@media\s*\(max-width:\s*\d+px\)[\s\S]*\.tec-filter-control\s*\{[^}]*width:\s*100%[^}]*flex-basis:\s*100%/s.test(read(reviewPath))],
  ['旧面板不再导入 promoteToGraph', !/import[^\n]*promoteToGraph/.test(read(panelPath))],
  ['旧面板不再调用 promoteToGraph', !/\bpromoteToGraph\s*\(/.test(read(panelPath))],
]

const failed = checks.filter(([, ok]) => !ok)
for (const [name, ok] of checks) console.log(`${ok ? 'PASS' : 'FAIL'} ${name}`)
if (failed.length) {
  console.error(`\n${failed.length} 项检查失败`)
  process.exit(1)
}
console.log(`\n全部 ${checks.length} 项检查通过`)
