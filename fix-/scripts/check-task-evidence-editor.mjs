import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const editorSfc = readFileSync(new URL('../src/components/task-evidence/TaskEvidenceGraphEditor.vue', import.meta.url), 'utf8')
const supportSfc = readFileSync(new URL('../src/components/task-evidence/TaskEvidenceSupportPanel.vue', import.meta.url), 'utf8')
import {
  normalizeCandidateGraph,
  buildEditableForest,
  findUnlinkedNodes,
  createEntity,
  updateEntity,
  removeEntity,
  attachNode,
  setEntityTrust,
  validateDraftClientSide,
} from '../src/utils/taskEvidenceCandidateGraph.js'

let assertions = 0
const equal = (...args) => { assertions += 1; assert.equal(...args) }
const deepEqual = (...args) => { assertions += 1; assert.deepEqual(...args) }
const match = (...args) => { assertions += 1; assert.match(...args) }
const ok = (...args) => { assertions += 1; assert.ok(...args) }

match(editorSfc, /readOnly:\s*\{\s*type:\s*Boolean/)
match(editorSfc, /defineEmits\(\['update:modelValue', 'dirty'\]\)/)
function extractFunctionBody(source, name) {
  const signature = new RegExp(`(?:async\\s+)?function\\s+${name}\\s*\\([^)]*\\)\\s*\\{`)
  const startMatch = signature.exec(source)
  assert.ok(startMatch, `missing function ${name}`)
  let index = startMatch.index + startMatch[0].length
  const bodyStart = index
  let depth = 1
  let quote = ''
  while (index < source.length && depth > 0) {
    const char = source[index]
    const next = source[index + 1]
    if (quote) {
      if (char === '\\') index += 2
      else if (char === quote) { quote = ''; index += 1 }
      else index += 1
      continue
    }
    if (char === '\'' || char === '"' || char === '`') { quote = char; index += 1; continue }
    if (char === '/' && next === '/') { index = source.indexOf('\n', index + 2); if (index < 0) break; continue }
    if (char === '/' && next === '*') { index = source.indexOf('*/', index + 2); index = index < 0 ? source.length : index + 2; continue }
    if (char === '{') depth += 1
    if (char === '}') depth -= 1
    index += 1
  }
  assert.equal(depth, 0, `unclosed function ${name}`)
  return source.slice(bodyStart, index - 1)
}

const mutationGuards = ['publish', 'add', 'startEdit', 'saveEdit', 'remove', 'toggleTrust', 'attach']
for (const handler of mutationGuards) {
  const body = extractFunctionBody(editorSfc, handler)
  match(body, /if \(props\.readOnly(?: \|\| !parentId)?\) return/)
}
const parentSelectMatches = [...editorSfc.matchAll(/<el-select :disabled="readOnly"[^>]*@change="attach\(node\.id, \$event\)"/g)]
equal(parentSelectMatches.length, 2)
match(editorSfc, /margin-left:calc\(var\(--tege-depth, 0\) \* var\(--tege-indent\)\)/)
match(editorSfc, /left:calc\(-1 \* var\(--tege-indent\)\)/)
match(editorSfc, /width:var\(--tege-indent\)/)
match(editorSfc, /--tege-indent:16px/)
match(editorSfc, /--tege-indent:28px/)
match(editorSfc, /tege-connector/)
match(editorSfc, /tege-branch/)
match(supportSfc, /<el-collapse-item/)
match(supportSfc, /minmax\(220px, 1fr\)/)
match(supportSfc, /overflow-wrap:anywhere/)
match(supportSfc, /resolvedAt: '解决时间'/)
match(supportSfc, /COMPLETED' \? '已完成'/)
match(supportSfc, /PARTIALLY_RESOLVED: '部分解决'/)
match(supportSfc, /checkpointConfirmed', 'aiPass'/)
match(supportSfc, /function isWide\(item\)[\s\S]*?'content'[\s\S]*?'safetyNote'/)
match(supportSfc, /'is-wide': isWide\(item\)/)
match(supportSfc, /\.tesp-step-number\s*\{[^}]*align-items:center[^}]*justify-content:center[^}]*line-height:1/)
match(supportSfc, /@media\s*\(min-width:\s*721px\)[\s\S]*?\.tesp-field\.is-wide\s*\{[^}]*grid-column:1 \/ -1[^}]*grid-template-columns:1fr/)
match(supportSfc, /@media\s*\(max-width:\s*720px\)[\s\S]*?\.tesp-field\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/)

const source = {
  ignoredTopLevel: true,
  devices: [{ id: 'device:1', name: '车', confirmed: true, evidence: [{ ref: 's1', excerpt: '原文', stepId: 'step:1', ignored: true }], ignored: true }],
  components: [{ id: 'component:1', name: '泵', confirmed: true, confirmationSource: 'ADMIN' }],
  faults: [{ id: 'fault:1', name: '漏液', confirmed: true }],
  solutions: [{ id: 'solution:1', name: '更换密封', verified: true, sourceType: 'confirmed', confirmationSource: 'ADMIN', ignored: true }],
  relations: [
    { sourceId: 'fault:1', targetId: 'solution:1', type: 'HAS_SOLUTION', confirmationSource: 'ADMIN', evidence: [{ ref: 'r3', excerpt: '方案证据', stepId: 'step:3', ignored: true }], ignored: true },
    { sourceId: 'device:1', targetId: 'component:1', type: 'OWNS', evidence: [{ ref: 'r1' }] },
    { sourceId: 'component:1', targetId: 'fault:1', type: 'CAUSES', evidence: [{ ref: 'r2' }] },
  ],
}

const graph = normalizeCandidateGraph(source)
deepEqual(Object.keys(graph), ['devices', 'components', 'faults', 'solutions', 'relations'])
deepEqual(Object.keys(graph.devices[0]), ['id', 'name', 'confirmed', 'evidence'])
deepEqual(graph.devices[0].evidence[0], { ref: 's1', excerpt: '原文', stepId: 'step:1' })
deepEqual(graph.relations[0], { sourceId: 'fault:1', targetId: 'solution:1', type: 'HAS_SOLUTION', confirmationSource: 'ADMIN', evidence: [{ ref: 'r3', excerpt: '方案证据', stepId: 'step:3' }] })
equal(graph.solutions[0].title, '更换密封')
source.devices[0].evidence[0].excerpt = '已污染'
source.relations[0].evidence[0].excerpt = '已污染'
equal(graph.devices[0].evidence[0].excerpt, '原文')
equal(graph.relations[0].evidence[0].excerpt, '方案证据')
graph.devices[0].evidence[0].excerpt = '输出修改'
equal(source.devices[0].evidence[0].excerpt, '已污染')

const forest = buildEditableForest(graph)
equal(forest.roots.length, 1)
equal(forest.roots[0].children[0].children[0].children[0].id, 'solution:1')
equal(forest.roots[0].children[0].relationType, 'OWNS')
deepEqual(forest.conflicts, [])
deepEqual(forest.unlinked, { devices: [], components: [], faults: [], solutions: [] })
forest.roots[0].children[0].children.push({ id: 'local-only' })
equal(forest.roots[0].children.length, 1)
equal(graph.components[0].children, undefined)

const multiRoot = normalizeCandidateGraph({
  devices: [{ id: 'device:a', name: 'A' }, { id: 'device:b', name: 'B' }],
  components: [{ id: 'component:x', name: 'X' }],
  faults: [{ id: 'fault:x', name: 'F' }],
  relations: [
    { sourceId: 'component:x', targetId: 'fault:x', type: 'CAUSES' },
    { sourceId: 'device:a', targetId: 'component:x', type: 'OWNS' },
    { sourceId: 'device:b', targetId: 'component:x', type: 'OWNS' },
    { sourceId: 'fault:x', targetId: 'component:x', type: 'OWNS' },
  ],
})
const conflictedForest = buildEditableForest(multiRoot)
equal(conflictedForest.roots.length, 2)
equal(conflictedForest.roots[0].children.length + conflictedForest.roots[1].children.length, 0)
deepEqual(conflictedForest.conflicts.map((item) => item.id), ['component:x'])
equal(conflictedForest.unlinked.faults[0].id, 'fault:x')

const duplicateEdges = normalizeCandidateGraph({
  devices: [{ id: 'device:duplicate', name: 'D' }],
  components: [{ id: 'component:duplicate', name: 'C' }],
  faults: [{ id: 'fault:duplicate', name: 'F' }],
  relations: [
    { sourceId: 'device:duplicate', targetId: 'component:duplicate', type: 'OWNS' },
    { sourceId: 'device:duplicate', targetId: 'component:duplicate', type: 'OWNS' },
    { sourceId: 'component:duplicate', targetId: 'fault:duplicate', type: 'CAUSES' },
    { sourceId: 'component:duplicate', targetId: 'fault:duplicate', type: 'CAUSES' },
  ],
})
const dedupedForest = buildEditableForest(duplicateEdges)
equal(dedupedForest.roots[0].children.length, 1)
equal(dedupedForest.roots[0].children[0].children.length, 1)

const childOnly = normalizeCandidateGraph({
  components: [{ id: 'component:orphan', name: '孤立部件' }],
  faults: [{ id: 'fault:child', name: '子故障' }],
  relations: [{ sourceId: 'component:orphan', targetId: 'fault:child', type: 'CAUSES' }],
})
const childOnlyUnlinked = findUnlinkedNodes(childOnly)
equal(childOnlyUnlinked.components[0].id, 'component:orphan')
equal(childOnlyUnlinked.faults.length, 0)

let created = normalizeCandidateGraph({})
created = createEntity(created, 'devices')
const deviceId = created.devices[0].id
created = createEntity(created, 'components', deviceId)
const componentId = created.components[0].id
created = createEntity(created, 'faults', componentId)
const faultId = created.faults[0].id
created = createEntity(created, 'solutions', faultId)
const solutionId = created.solutions[0].id
match(deviceId, /^device:/)
match(componentId, /^component:/)
match(faultId, /^fault:/)
match(solutionId, /^solution:/)
deepEqual(created.relations.map((item) => item.type), ['OWNS', 'CAUSES', 'HAS_SOLUTION'])
equal(new Set([deviceId, componentId, faultId, solutionId]).size, 4)

const existingIds = new Set()
let many = normalizeCandidateGraph({})
for (let index = 0; index < 100; index += 1) {
  many = createEntity(many, 'devices')
  existingIds.add(many.devices.at(-1).id)
}
equal(existingIds.size, 100)

const attachBase = normalizeCandidateGraph({
  devices: [{ id: 'device:old', name: '旧' }, { id: 'device:new', name: '新' }],
  components: [{ id: 'component:move', name: '移动' }],
  faults: [{ id: 'fault:attach', name: '待挂接' }],
  relations: [{ sourceId: 'device:old', targetId: 'component:move', type: 'OWNS' }],
})
const moved = attachNode(attachBase, 'component:move', 'device:new')
deepEqual(moved.relations.filter((item) => item.targetId === 'component:move').map((item) => item.sourceId), ['device:new'])
const attached = attachNode(moved, 'fault:attach', 'component:move')
equal(attached.relations.some((item) => item.sourceId === 'component:move' && item.targetId === 'fault:attach' && item.type === 'CAUSES'), true)
equal(attachNode(attached, 'fault:attach', 'component:move').relations.length, attached.relations.length)
equal(attachNode(attached, 'fault:attach', 'device:new').relations.length, attached.relations.length)
equal(attachBase.relations[0].sourceId, 'device:old')

const trustedSolution = setEntityTrust(created, 'solutions', solutionId, true)
deepEqual(
  { verified: trustedSolution.solutions[0].verified, sourceType: trustedSolution.solutions[0].sourceType, confirmationSource: trustedSolution.solutions[0].confirmationSource },
  { verified: true, sourceType: 'confirmed', confirmationSource: 'ADMIN' },
)
const revokedSolution = setEntityTrust(trustedSolution, 'solutions', solutionId, false)
equal(revokedSolution.solutions[0].verified, false)
equal('confirmationSource' in revokedSolution.solutions[0], false)
equal('sourceType' in revokedSolution.solutions[0], false)

const extractedSolution = normalizeCandidateGraph({ solutions: [{ id: 'solution:raw', title: '原方案', verified: false, sourceType: 'extracted' }] })
const revokedRaw = setEntityTrust(extractedSolution, 'solutions', 'solution:raw', false)
equal(revokedRaw.solutions[0].sourceType, 'extracted')
const trustedFault = setEntityTrust(created, 'faults', faultId, true)
equal(trustedFault.faults[0].confirmed, true)
equal(trustedFault.faults[0].confirmationSource, 'ADMIN')
const revokedFault = setEntityTrust(trustedFault, 'faults', faultId, false)
equal(revokedFault.faults[0].confirmed, false)
equal('confirmationSource' in revokedFault.faults[0], false)

const patched = updateEntity(graph, 'devices', 'device:1', { name: '新车', ignored: true, evidence: [{ ref: 'new', excerpt: '新证据', ignored: true }] })
equal(patched.devices[0].name, '新车')
deepEqual(patched.devices[0].evidence, [{ ref: 'new', excerpt: '新证据' }])
equal('ignored' in patched.devices[0], false)
const deleted = removeEntity(created, componentId)
equal(deleted.components.length, 0)
equal(deleted.relations.some((item) => item.sourceId === componentId || item.targetId === componentId), false)
equal(created.components.length, 1)

const valid = validateDraftClientSide(graph)
equal(valid.valid, true)
equal(valid.precheckOnly, true)
const reviewableButDisconnected = normalizeCandidateGraph({
  devices: [{ id: 'device:review', name: 'D', confirmed: true }],
  components: [{ id: 'component:review', name: 'C', confirmed: true }],
  faults: [{ id: 'fault:review', name: 'F', confirmed: true }],
  solutions: [{ id: 'solution:review', title: 'S', verified: true, sourceType: 'confirmed' }],
  relations: [],
})
const disconnectedValidation = validateDraftClientSide(reviewableButDisconnected)
equal(disconnectedValidation.valid, false)
equal(disconnectedValidation.precheckOnly, true)

const invalidGraphs = [
  { devices: [{ id: 'bad id', name: 'D' }] },
  { devices: [{ id: 'd', name: ' ' }] },
  { devices: Array.from({ length: 201 }, (_, index) => ({ id: `d:${index}`, name: 'D' })) },
  { devices: [{ id: 'same', name: 'D' }], components: [{ id: 'same', name: 'C' }] },
  { devices: [{ id: 'd', name: 'D', confirmed: 'yes' }] },
  { devices: [{ id: 'd', name: 'D', evidence: [{ ref: '' }] }] },
  { devices: [{ id: 'd', name: 'D' }], relations: [{ sourceId: 'd', targetId: 'missing', type: 'OWNS' }] },
  { devices: [{ id: 'd', name: 'D' }], faults: [{ id: 'f', name: 'F' }], relations: [{ sourceId: 'd', targetId: 'f', type: 'CAUSES' }] },
  { devices: [{ id: 'd', name: 'D' }], components: [{ id: 'c', name: 'C' }], relations: [{ sourceId: 'd', targetId: 'c', type: 'OWNS' }, { sourceId: 'd', targetId: 'c', type: 'OWNS' }] },
  { solutions: [{ id: 's', title: 'S', verified: true, sourceType: 'manual' }] },
]
for (const candidate of invalidGraphs) equal(validateDraftClientSide(candidate).valid, false)

ok(assertions >= 50)
console.log(`PASS task evidence editor checks: ${assertions}`)
