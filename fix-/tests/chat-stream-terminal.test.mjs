import test from 'node:test'
import assert from 'node:assert/strict'

import {
  INCOMPLETE_STREAM_ERROR_NAME,
  INCOMPLETE_STREAM_MESSAGE,
  ensureTerminalStreamEvent,
} from '../src/utils/chatStreamTerminal.js'

test('accepts a completed stream with a done event', () => {
  assert.doesNotThrow(() => ensureTerminalStreamEvent({ doneReceived: true }))
})

test('accepts a stream terminated by an explicit error event', () => {
  assert.doesNotThrow(() => ensureTerminalStreamEvent({ errorReceived: true }))
})

test('rejects an EOF without a done or error event', () => {
  assert.throws(
    () => ensureTerminalStreamEvent({ doneReceived: false, errorReceived: false }),
    (error) => {
      assert.equal(error.name, INCOMPLETE_STREAM_ERROR_NAME)
      assert.equal(error.message, INCOMPLETE_STREAM_MESSAGE)
      return true
    },
  )
})
