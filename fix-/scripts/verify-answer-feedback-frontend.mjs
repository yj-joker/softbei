import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import {
  mergeEvidenceRefsForSave,
  splitEvidenceRefsForEditing,
} from '../src/utils/domainRuleEvidenceRefs.js'

const root = resolve(import.meta.dirname, '..')

function read(path) {
  const fullPath = resolve(root, path)
  if (!existsSync(fullPath)) throw new Error(`Missing file: ${path}`)
  return readFileSync(fullPath, 'utf8')
}

function expectIncludes(file, text) {
  if (!read(file).includes(text)) throw new Error(`${file} should include ${text}`)
}

expectIncludes('src/api/answerFeedback.js', 'submitAnswerFeedback')
expectIncludes('src/api/answerFeedback.js', 'getAnswerFeedbackPage')
expectIncludes('src/api/answerFeedback.js', 'getAnswerFeedbackDetail')
expectIncludes('src/api/answerFeedback.js', 'convertAnswerFeedback')
expectIncludes('src/api/answerFeedback.js', 'dismissAnswerFeedback')
expectIncludes('src/components/ai/ChatMessage.vue', "emit('report-answer', message)")
expectIncludes('src/components/ai/ChatMessage.vue', 'Boolean(props.message.persistedMessageId)')
expectIncludes('src/components/ai/AnswerFeedbackDialog.vue', '答案纠错')
expectIncludes('src/components/ai/AnswerFeedbackDialog.vue', 'assistantMessageId: props.message.persistedMessageId')
expectIncludes('src/components/ai/AnswerFeedbackDialog.vue', '!props.message?.persistedMessageId')
expectIncludes('src/components/AIChat.vue', '@report-answer="openFeedback"')
expectIncludes('src/stores/aiChatStore.js', 'markFeedbackSubmitted')
expectIncludes('src/stores/aiChatStore.js', 'assistant.persistedMessageId = data.assistantMessageId')
expectIncludes('src/views/adminViews/AdminKnowledgeCenter.vue', "name: 'answer-feedback'")
expectIncludes('src/views/adminViews/AdminAnswerFeedback.vue', '转为规则草稿')
expectIncludes('src/views/adminViews/AdminAnswerFeedback.vue', 'convertAnswerFeedback')
expectIncludes('src/views/adminViews/AdminAnswerFeedback.vue', 'getAnswerFeedbackDetail')
expectIncludes('src/views/adminViews/AdminAnswerFeedback.vue', '<el-button link :icon="View" @click="openDetail(row)">查看详情</el-button>')
expectIncludes('src/views/adminViews/AdminAnswerFeedback.vue', '反馈详情')
expectIncludes('src/views/adminViews/AdminAnswerFeedback.vue', 'detail.originalQuestion')
expectIncludes('src/views/adminViews/AdminAnswerFeedback.vue', 'detail.originalAnswer')
expectIncludes('src/views/adminViews/AdminAnswerFeedback.vue', 'detail.correctedAnswer')
expectIncludes('src/views/adminViews/AdminAnswerFeedback.vue', 'detail.processedById')
expectIncludes('src/views/adminViews/AdminAnswerFeedback.vue', 'detail.domainRuleId')
expectIncludes('src/views/adminViews/AdminAnswerFeedback.vue', "tab: 'domain-rules'")
expectIncludes('src/views/adminViews/AdminDomainRules.vue', 'route.query.ruleId')

const provenance = {
  source: 'answer_feedback',
  feedback_id: '88',
  assistant_message_id: '701',
  session_id: '101',
  device_type: 'engine',
  document_id: 'manual-doc',
  original_question: 'question',
}
const split = splitEvidenceRefsForEditing([
  provenance,
  { source: 'manual', section: 'inspection', page: 3 },
])
const merged = mergeEvidenceRefsForSave('manual ｜ updated inspection ｜ 第4页', split.preserved)
if (JSON.stringify(merged[0]) !== JSON.stringify(provenance)) {
  throw new Error('answer feedback provenance must survive domain-rule editing unchanged')
}
if (merged[1]?.page !== 4) {
  throw new Error('editable evidence refs should still be parsed from text')
}

console.log('answer feedback frontend verification passed')
