<script setup>
import KnowledgeGraphExplorer from '../../components/KnowledgeGraphExplorer.vue'
</script>

<template>
  <div class="kg-page">
    <!-- 普通用户端：只读浏览，无审核动作 -->
    <KnowledgeGraphExplorer :readonly="true" />
  </div>
</template>

<style scoped>
.kg-page { position: relative; width: 100%; height: 100%; min-height: 560px; }
</style>
