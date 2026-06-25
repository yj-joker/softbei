import { ref } from 'vue'

/**
 * 全局主题切换（换肤）。
 *
 * 原理：全站颜色都走 base.css 的 --plaza- 与 --el- 等 CSS 变量，
 * 切主题 = 在 <html> 上设 data-theme，覆盖那一套变量值，组件零改动。
 * 默认(无 data-theme)= 暖陶土。偏好持久化到 localStorage。
 */

export const THEMES = [
  { key: 'warm', name: '暖陶土', color: '#c4602f' },
  { key: 'cool', name: '青灰墨', color: '#2f7488' },
  { key: 'sage', name: '松石绿', color: '#4f7d3a' },
]

const STORAGE_KEY = 'wx_theme'
const VALID = new Set(THEMES.map((t) => t.key))

const current = ref('warm')

/** 把主题应用到 <html>：warm=默认(移除 data-theme)，其它=设 data-theme。 */
function apply(themeKey) {
  const key = VALID.has(themeKey) ? themeKey : 'warm'
  current.value = key
  const root = document.documentElement
  if (key === 'warm') root.removeAttribute('data-theme')
  else root.setAttribute('data-theme', key)
}

/** 应用启动时调用：从 localStorage 恢复主题（在 mount 前调用避免闪烁）。 */
export function initTheme() {
  let saved = 'warm'
  try { saved = localStorage.getItem(STORAGE_KEY) || 'warm' } catch (e) { /* ignore */ }
  apply(saved)
}

export function useTheme() {
  function setTheme(themeKey) {
    apply(themeKey)
    try { localStorage.setItem(STORAGE_KEY, current.value) } catch (e) { /* ignore */ }
  }
  return { current, themes: THEMES, setTheme }
}
