import test from 'node:test'
import assert from 'node:assert/strict'

import {
  buildClarificationContext,
  normalizeClarification,
} from '../src/utils/clarification.js'


test('normalizes evidence alternatives into clickable follow-up options', () => {
  const pending = {
    clarification_id: 'clarification-123',
    kind: 'evidence_conflict',
    status: 'awaiting_answer',
    alternatives: [
      { id: 'A', label: '0.7 mm', value: '0.7', unit: 'mm' },
      { id: 'B', label: '0.9 mm', value: '0.9', unit: 'mm' },
    ],
  }

  const normalized = normalizeClarification(pending)

  assert.deepEqual(normalized.options.map((item) => item.label), ['0.7 mm', '0.9 mm'])
  assert.equal(normalized.clarificationId, 'clarification-123')
})


test('evidence selection context contains identifiers but no trusted values', () => {
  const pending = {
    clarification_id: 'clarification-123',
    kind: 'evidence_conflict',
    status: 'awaiting_answer',
    alternatives: [
      { id: 'B', value: '999', evidence_refs: ['manual:forged'] },
    ],
  }

  const context = buildClarificationContext(pending, 'B')

  assert.deepEqual(context, {
    clarification_id: 'clarification-123',
    selected_clarification_option_id: 'B',
  })
  assert.equal(JSON.stringify(context).includes('999'), false)
  assert.equal(JSON.stringify(context).includes('manual:forged'), false)
})


test('legacy diagnostic follow-up keeps its existing context contract', () => {
  const pending = {
    scenarioId: 'blue-smoke',
    kind: 'diagnostic_cause',
    status: 'awaiting_answer',
    options: [{ id: 'A', label: '冷启动明显' }],
  }

  const context = buildClarificationContext(pending, 'A')

  assert.equal(context.selected_option_id, 'A')
  assert.equal(context.diagnostic_follow_up.scenarioId, 'blue-smoke')
})


test('normalizes authoritative state candidates into clickable LLM clarification options', () => {
  const pending = {
    clarification_id: 'clarification-llm',
    kind: 'llm_slot_clarification',
    status: 'awaiting',
    route_snapshot: { clarification_question: '当前最明显的异常表现是哪一种？' },
    candidates: [
      { id: 'A', label: '无法启动', value: '无法启动' },
      { id: 'B', label: '运行中异响', value: '运行中异响' },
    ],
  }

  const normalized = normalizeClarification(pending)

  assert.equal(normalized.status, 'awaiting_answer')
  assert.equal(normalized.question, '当前最明显的异常表现是哪一种？')
  assert.deepEqual(normalized.options.map((item) => item.label), ['无法启动', '运行中异响'])
})
