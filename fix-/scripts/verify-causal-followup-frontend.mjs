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

expectIncludes('src/api/aiChat.js', 'context = undefined')
expectIncludes('src/api/aiChat.js', 'context,')
expectIncludes('src/stores/aiChatStore.js', 'diagnosticFollowUp')
expectIncludes('src/stores/aiChatStore.js', 'assistant.diagnosticFollowUp = data.diagnosticFollowUp')
expectIncludes('src/components/AIChat.vue', '@send-follow-up="handleFollowUpSend"')
expectIncludes('src/components/AIChat.vue', 'pendingFollowUp')
expectIncludes('src/components/ai/ChatMessage.vue', "defineEmits(['open-agent', 'send-follow-up'])")
expectIncludes('src/components/ai/ChatMessage.vue', 'followUpOptions')
expectIncludes('src/components/ai/ChatMessage.vue', 'follow-up-card')
expectIncludes('src/components/ai/ChatMessage.vue', 'isFollowUpSubmitted')
expectIncludes('src/components/ai/ChatMessage.vue', 'follow-up-submitted')
expectIncludes('src/utils/agentTimeline.js', 'causal_follow_up')

console.log('causal follow-up frontend verification passed')
