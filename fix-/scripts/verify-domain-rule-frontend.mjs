import { readFileSync, existsSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(import.meta.dirname, '..')

function read(path) {
  const fullPath = resolve(root, path)
  if (!existsSync(fullPath)) {
    throw new Error(`Missing file: ${path}`)
  }
  return readFileSync(fullPath, 'utf8')
}

function expectIncludes(file, text) {
  const content = read(file)
  if (!content.includes(text)) {
    throw new Error(`${file} should include ${text}`)
  }
}

function expectNotIncludes(file, text) {
  const content = read(file)
  if (content.includes(text)) {
    throw new Error(`${file} should not include ${text}`)
  }
}

const apiExports = [
  'getDomainRulePage',
  'getDomainRuleDetail',
  'createDomainRule',
  'updateDomainRule',
  'submitDomainRule',
  'approveDomainRule',
  'rejectDomainRule',
  'disableDomainRule',
  'retrySyncDomainRule',
]

for (const name of apiExports) {
  expectIncludes('src/api/domainRule.js', `function ${name}`)
}

expectIncludes('src/api/domainRule.js', "const PREFIX = '/weixiu/domain-rule'")
expectIncludes('src/api/domainRule.js', '`${PREFIX}/page`')
expectIncludes('src/router/index.js', 'AdminDomainRules')
expectIncludes('src/router/index.js', "tab: 'domain-rules'")
expectNotIncludes('src/router/index.js', "component: () => import('../views/adminViews/AdminDomainRules.vue')")
expectNotIncludes('src/views/adminViews/AdminLayout.vue', '/admin/domain-rules')
expectIncludes('src/views/adminViews/AdminKnowledgeCenter.vue', "import AdminDomainRules from './AdminDomainRules.vue'")
expectIncludes('src/views/adminViews/AdminKnowledgeCenter.vue', "name: 'domain-rules'")
expectIncludes('src/views/adminViews/AdminKnowledgeCenter.vue', 'component: AdminDomainRules')
expectIncludes('src/views/adminViews/AdminDomainRules.vue', '诊断规则')
expectIncludes('src/views/adminViews/AdminDomainRules.vue', 'rule-form-dialog')
expectIncludes('src/views/adminViews/AdminDomainRules.vue', 'submitDomainRule')
expectIncludes('src/views/adminViews/AdminDomainRules.vue', 'approveDomainRule')
expectIncludes('src/views/adminViews/AdminDomainRules.vue', 'retrySyncDomainRule')
expectIncludes('src/views/adminViews/AdminDomainRules.vue', 'rejected: 0')
expectIncludes('src/views/adminViews/AdminDomainRules.vue', 'disabled: 0')
expectIncludes('src/views/adminViews/AdminDomainRules.vue', "status: 'rejected'")
expectIncludes('src/views/adminViews/AdminDomainRules.vue', "status: 'disabled'")
expectIncludes('src/views/adminViews/AdminDomainRules.vue', "switchStatus('rejected')")
expectIncludes('src/views/adminViews/AdminDomainRules.vue', "switchStatus('disabled')")
expectIncludes('src/views/adminViews/AdminDomainRules.vue', '专家经验录入')
expectIncludes('src/views/adminViews/AdminDomainRules.vue', '核心信息')
expectIncludes('src/views/adminViews/AdminDomainRules.vue', '故障现象')
expectIncludes('src/views/adminViews/AdminDomainRules.vue', '专家判断')
expectIncludes('src/views/adminViews/AdminDomainRules.vue', '自动命中条件')
expectIncludes('src/views/adminViews/AdminDomainRules.vue', '更多规则设置')
expectIncludes('src/views/adminViews/AdminDomainRules.vue', 'advancedPanels')
expectIncludes('src/views/adminViews/AdminDomainRules.vue', 'autoConditionText')
expectIncludes('src/views/adminViews/AdminDomainRules.vue', 'parseEvidenceRefs')
expectNotIncludes('src/views/adminViews/AdminDomainRules.vue', 'label="命中条件" prop="conditionText"')

console.log('domain rule frontend verification passed')
