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
expectIncludes('src/router/index.js', 'AdminDomainRules.vue')
expectIncludes('src/views/adminViews/AdminLayout.vue', '/admin/domain-rules')
expectIncludes('src/views/adminViews/AdminDomainRules.vue', '诊断规则')
expectIncludes('src/views/adminViews/AdminDomainRules.vue', 'rule-form-dialog')
expectIncludes('src/views/adminViews/AdminDomainRules.vue', 'submitDomainRule')
expectIncludes('src/views/adminViews/AdminDomainRules.vue', 'approveDomainRule')
expectIncludes('src/views/adminViews/AdminDomainRules.vue', 'retrySyncDomainRule')

console.log('domain rule frontend verification passed')
