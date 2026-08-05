import fs from 'node:fs'
import path from 'node:path'

const root = path.dirname(path.dirname(decodeURIComponent(new URL(import.meta.url).pathname).replace(/^\/(\w):/, '$1:')))
const files = {
  api: fs.readFileSync(path.join(root, 'src/api/maintenanceTask.js'), 'utf8'),
  status: fs.readFileSync(path.join(root, 'src/constants/taskStatus.js'), 'utf8'),
  detail: fs.readFileSync(path.join(root, 'src/views/userViews/UserTaskDetail.vue'), 'utf8'),
  dialog: fs.existsSync(path.join(root, 'src/components/task/TaskResolutionDialog.vue'))
    ? fs.readFileSync(path.join(root, 'src/components/task/TaskResolutionDialog.vue'), 'utf8')
    : '',
}

const checks = [
  ['API confirmTaskResolution', /export function confirmTaskResolution\s*\(taskId,\s*data\)/],
  ['API resolution endpoint', /\/resolution['"`]/],
  ['API throwOnError', /confirmTaskResolution[\s\S]{0,300}throwOnError:\s*true/],
  ['RESOLUTION_PENDING status', /RESOLUTION_PENDING/],
  ['resolution dialog component', /TaskResolutionDialog/],
  ['dialog resolutionStatus', /resolutionStatus/],
  ['dialog result fields', /finalFaultCause[\s\S]*effectiveMeasure[\s\S]*completionSummary/],
  ['detail CLOSED extraction status', /extractionStatus/],
  ['detail pending non-executing', /RESOLUTION_PENDING[\s\S]{0,800}(stepExecuting|executing)[\s\S]{0,120}EXECUTING/],
  ['voice update all done refreshes task', /onVoiceUpdated[\s\S]{0,900}DONE_SET[\s\S]{0,500}await load\(\)/],
  ['voice update exits voice state', /onVoiceUpdated[\s\S]{0,900}(readAlong\.exit|voiceMode\.value\s*=\s*false)/],
]

const failed = checks.filter(([name, pattern]) => !pattern.test(Object.values(files).join('\n')))
if (failed.length) {
  console.error(`Task resolution contract RED: ${failed.length} check(s) failed`)
  for (const [name] of failed) console.error(`- ${name}`)
  process.exit(1)
}
console.log(`Task resolution contract GREEN: ${checks.length} checks passed`)
