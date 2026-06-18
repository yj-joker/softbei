<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'

const props = defineProps({
  items: {
    type: Array,
    default: () => []
  },
  autoplay: {
    type: Boolean,
    default: false
  },
  interval: {
    type: Number,
    default: 4000
  }
})

const current = ref(0)
const isAnim = ref(false)
let slideshowTimer = null

const itemsCount = computed(() => props.items.length)
const hasEnoughItems = computed(() => itemsCount.value >= 3)

const $leftItm = computed(() => current.value === 0 ? itemsCount.value - 1 : current.value - 1)
const $rightItm = computed(() => current.value === itemsCount.value - 1 ? 0 : current.value + 1)
const $nextItm = computed(() => $rightItm.value === itemsCount.value - 1 ? 0 : $rightItm.value + 1)
const $prevItm = computed(() => $leftItm.value === 0 ? itemsCount.value - 1 : $leftItm.value - 1)

function getItemClass(index) {
  if (index === current.value) return 'dg-center'
  if (index === $leftItm.value) return 'dg-left'
  if (index === $rightItm.value) return 'dg-right'
  if (index === $nextItm.value) return 'dg-next-item'
  if (index === $prevItm.value) return 'dg-prev-item'
  return 'dg-out'
}

function navigate(dir) {
  if (isAnim.value) return
  isAnim.value = true

  if (dir === 'next') {
    current.value = $rightItm.value
  } else {
    current.value = $leftItm.value
  }

  clearTimeout(slideshowTimer)
  if (props.autoplay) {
    slideshowTimer = setTimeout(() => {
      startSlideshow()
    }, props.interval)
  }

  setTimeout(() => {
    isAnim.value = false
  }, 400)
}

function prev() {
  navigate('prev')
}

function next() {
  navigate('next')
}

function onLeftClick() {
  prev()
}

function onRightClick() {
  next()
}

function startSlideshow() {
  if (!props.autoplay) return
  clearTimeout(slideshowTimer)
  slideshowTimer = setTimeout(() => {
    navigate('next')
    startSlideshow()
  }, props.interval)
}

onMounted(() => {
  if (props.autoplay && hasEnoughItems.value) {
    startSlideshow()
  }
})

onBeforeUnmount(() => {
  clearTimeout(slideshowTimer)
})
</script>

<template>
  <div v-if="hasEnoughItems" class="banner-container">
    <div class="dg-container">
      <!-- Left click zone -->
      <div class="click-zone click-zone-left" @click="onLeftClick"></div>

      <!-- Right click zone -->
      <div class="click-zone click-zone-right" @click="onRightClick"></div>

      <div class="dg-wrapper">
        <a
          v-for="(item, index) in items"
          :key="index"
          :href="item.href || '#'"
          :title="item.title || ''"
          class="dg-item"
          :class="getItemClass(index)"
          target="_blank"
        >
          <img :src="item.src" :alt="item.title || ''" />
        </a>
      </div>
    </div>
  </div>
</template>

<style scoped>
.banner-container {
  width: calc(100% + 72px);
  margin: 0 -36px 32px;
  perspective: 1200px;
}

.dg-container {
  position: relative;
  width: 100%;
  height: 540px;
  overflow: hidden;
}

.dg-wrapper {
  position: relative;
  width: 100%;
  height: 100%;
  transform-style: preserve-3d;
}

/* Click zones - invisible overlay on left/right thirds */
.click-zone {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 35%;
  z-index: 1000;
  cursor: pointer;
}

.click-zone-left {
  left: 0;
}

.click-zone-right {
  right: 0;
}

.dg-item {
  position: absolute;
  left: 50%;
  top: 50%;
  width: 480px;
  height: 400px;
  margin-left: -240px;
  margin-top: -200px;
  opacity: 0;
  visibility: hidden;
  transition: all 0.4s ease;
  text-decoration: none;
  display: block;
}

.dg-item img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  border-radius: 8px;
  pointer-events: none;
}

/* 中间主图 - 最顶层，横向拉宽，垂直端正 */
.dg-center {
  opacity: 1 !important;
  visibility: visible !important;
  z-index: 999;
  width: 820px !important;
  height: 480px !important;
  margin-left: -410px !important;
  margin-top: -240px !important;
  transform: translateX(0) translateZ(200px) rotateY(0deg) scale(1) !important;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

/* 左侧图片 - 向左偏移，露出在左侧 */
.dg-left {
  opacity: 1 !important;
  visibility: visible !important;
  z-index: 1;
  transform: translateX(-480px) translateZ(-300px) rotateY(30deg) scale(0.78) !important;
  cursor: pointer;
}

/* 右侧图片 - 向右偏移，露出在右侧 */
.dg-right {
  opacity: 1 !important;
  visibility: visible !important;
  z-index: 1;
  transform: translateX(480px) translateZ(-300px) rotateY(-30deg) scale(0.78) !important;
  cursor: pointer;
}

/* 即将进入的两张 - 极小，极低 */
.dg-prev-item {
  opacity: 0 !important;
  visibility: hidden !important;
  transform: translateX(-600px) translateZ(-400px) rotateY(30deg) scale(0.5) !important;
}

.dg-next-item {
  opacity: 0 !important;
  visibility: hidden !important;
  transform: translateX(600px) translateZ(-400px) rotateY(-30deg) scale(0.5) !important;
}

.dg-out {
  opacity: 0 !important;
  visibility: hidden !important;
}

@media (max-width: 1024px) {
  .dg-container {
    height: 420px;
  }

  .dg-center {
    width: 680px !important;
    height: 400px !important;
    margin-left: -340px !important;
    margin-top: -200px !important;
  }

  .dg-item {
    width: 480px;
    height: 360px;
    margin-left: -240px;
    margin-top: -180px;
  }

  .dg-left {
    transform: translateX(150px) translateZ(-250px) rotateY(30deg) scale(0.72) !important;
  }

  .dg-right {
    transform: translateX(-150px) translateZ(-250px) rotateY(-30deg) scale(0.72) !important;
  }

  .dg-prev-item,
  .dg-next-item {
    display: none;
  }
}

@media (max-width: 768px) {
  .dg-container {
    height: 340px;
  }

  .dg-center {
    width: 460px !important;
    height: 300px !important;
    margin-left: -230px !important;
    margin-top: -150px !important;
  }

  .dg-item {
    width: 320px;
    height: 240px;
    margin-left: -160px;
    margin-top: -120px;
  }

  .dg-left {
    transform: translateX(100px) translateZ(-180px) rotateY(30deg) scale(0.65) !important;
  }

  .dg-right {
    transform: translateX(-100px) translateZ(-180px) rotateY(-30deg) scale(0.65) !important;
  }

  .dg-prev-item,
  .dg-next-item {
    display: none;
  }
}
</style>