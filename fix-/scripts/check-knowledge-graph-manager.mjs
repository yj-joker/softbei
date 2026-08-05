import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('../src/components/KnowledgeGraphManager.vue', import.meta.url), 'utf8')
const expectedClick = '@click="openCreate(CHILD[ui.selected.type].child, key(ui.selected.type, ui.selected.rawId))"'

if (!source.includes(expectedClick)) {
  throw new Error('子节点创建未传递 nodeMap 内部父键')
}

const start = source.indexOf('async function submitForm()')
const end = source.indexOf('\nasync function removeSelected()', start)
const submit = source.slice(start, end)
const guard = /if \(dlg\.mode === 'create' && dlg\.type !== 'device'\)[\s\S]*?nodeMap\.get\(dlg\.parentKey\)/

if (!guard.test(submit)) {
  throw new Error('子节点保存前缺少有效父节点校验')
}
if (!submit.includes("if (!newId) throw new Error('保存接口未返回实体 ID')")) {
  throw new Error('保存响应缺失 ID 时仍可能误报成功')
}

if (!/useRoute/.test(source) || !/route\.query\.taskId/.test(source)) {
  throw new Error('图谱管理器未读取任务来源路由上下文')
}
if (!/getTaskSourceGraph/.test(source) || !/async function loadTaskContextGraph/.test(source)) {
  throw new Error('图谱管理器未通过任务来源接口加载精确子图')
}
if (/route\.query\.deviceName/.test(source)) {
  throw new Error('任务图谱仍依赖设备名推测，不是精确来源查询')
}
if (!/来源任务/.test(source)) {
  throw new Error('图谱管理器未展示来源任务上下文')
}

console.log('PASS: KnowledgeGraphManager 子节点关系创建与任务定位检查')
