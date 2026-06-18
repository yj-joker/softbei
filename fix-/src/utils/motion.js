// 统一的 GSAP 入场动效工具：外壳与页面共用，保证整站动效语言一致。
// 所有方法都对「元素不存在 / 用户偏好减少动效」做了安全降级。
import { gsap } from 'gsap'

const prefersReduced =
  typeof window !== 'undefined' &&
  window.matchMedia &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches

const EASE = 'power3.out'

// 侧边外壳入场：悬浮岛整体滑入落位 → 品牌→标签→导航逐项 stagger 上浮
export function enterShell(root) {
  if (prefersReduced || !root) return
  const q = (sel) => root.querySelectorAll(sel)
  const tl = gsap.timeline({ defaults: { ease: EASE } })
  // 1) 悬浮岛从左侧滑入 + 轻微缩放回弹落位（expo 收尾，有「飞入归位」的手感）
  tl.from(root, {
    x: -52,
    autoAlpha: 0,
    scale: 0.95,
    transformOrigin: 'left center',
    duration: 0.66,
    ease: 'expo.out',
  })
    // 2) 岛内元素依次浮现
    .from(root.querySelector('.brand'), { x: -18, opacity: 0, duration: 0.5 }, '-=0.36')
    .from(root.querySelector('.rail-tag'), { x: -14, opacity: 0, duration: 0.4 }, '-=0.32')
    .from(
      q('.nav-item'),
      { x: -16, opacity: 0, duration: 0.42, stagger: 0.055 },
      '-=0.24',
    )
    .from(root.querySelector('.rail-foot'), { y: 14, opacity: 0, duration: 0.4 }, '-=0.2')
  return tl
}

// 顶栏 + 内容区入场
export function enterMain(topbar, content) {
  if (prefersReduced) return
  const tl = gsap.timeline({ defaults: { ease: EASE } })
  if (topbar) tl.from(topbar, { y: -16, opacity: 0, duration: 0.45 })
  if (content) tl.from(content, { y: 18, opacity: 0, duration: 0.55 }, '-=0.25')
  return tl
}

// 通用：一组元素逐项上浮
export function revealStagger(targets, opts = {}) {
  if (prefersReduced || !targets) return
  return gsap.from(targets, {
    y: opts.y ?? 22,
    opacity: 0,
    duration: opts.duration ?? 0.55,
    ease: EASE,
    stagger: opts.stagger ?? 0.08,
    delay: opts.delay ?? 0,
  })
}
