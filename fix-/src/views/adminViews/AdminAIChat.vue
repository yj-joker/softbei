<script>
// 显式命名，供 Layout 的 <keep-alive include="AdminAIChat"> 精确匹配（切页不中断输出）
export default { name: 'AdminAIChat' }
</script>

<script setup>
import AIChat from '@/components/AIChat.vue'
</script>

<template>
  <AIChat storage-key="admin-ai-sessions" />
</template>