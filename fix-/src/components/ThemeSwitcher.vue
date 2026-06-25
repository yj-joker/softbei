<script setup>
import { Brush, Check } from '@element-plus/icons-vue'
import { useTheme } from '@/composables/useTheme'

const { current, themes, setTheme } = useTheme()
</script>

<template>
  <el-dropdown trigger="click" @command="setTheme">
    <button type="button" class="theme-trigger" title="切换配色主题" aria-label="切换配色主题">
      <el-icon><Brush /></el-icon>
    </button>
    <template #dropdown>
      <el-dropdown-menu class="theme-menu">
        <el-dropdown-item
          v-for="t in themes"
          :key="t.key"
          :command="t.key"
          :class="{ active: current === t.key }"
        >
          <span class="swatch" :style="{ background: t.color }" />
          <span class="t-name">{{ t.name }}</span>
          <el-icon v-if="current === t.key" class="t-check"><Check /></el-icon>
        </el-dropdown-item>
      </el-dropdown-menu>
    </template>
  </el-dropdown>
</template>

<style scoped>
.theme-trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border: 1px solid var(--plaza-border);
  border-radius: 9px;
  color: var(--plaza-text-muted);
  background: var(--plaza-bg-card);
  cursor: pointer;
  transition: border-color 0.18s ease, color 0.18s ease, background 0.18s ease;
}
.theme-trigger:hover {
  color: var(--plaza-accent);
  border-color: var(--plaza-accent);
  background: var(--plaza-accent-soft);
}
.theme-menu .swatch {
  display: inline-block;
  width: 14px;
  height: 14px;
  margin-right: 9px;
  border-radius: 4px;
  border: 1px solid rgba(0, 0, 0, 0.08);
  vertical-align: -2px;
}
.theme-menu .t-name { font-size: 13px; }
.theme-menu .t-check { margin-left: 10px; color: var(--plaza-accent); }
.theme-menu :deep(.el-dropdown-menu__item.active) { color: var(--plaza-accent); font-weight: 600; }
</style>
