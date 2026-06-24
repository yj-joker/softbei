<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { gsap } from 'gsap'
import {
  generateQuiz, practiceQuiz, getQuizSession, submitQuiz,
  saveQuizToBank, listQuizBank, listQuizMastery,
} from '@/api/quiz'

// 视图：home（入口）| answering（答题）| result（成绩解析）
const view = ref('home')
const loading = ref(false)
const generating = ref(false)   // AI 出题异步生成中
const genError = ref('')

const sessionId = ref(null)
const mode = ref('')            // AI_GENERATE / BANK_PRACTICE
const questions = ref([])       // 当前会话题目
const answers = reactive({})    // { [questionId]: 'A' | ['A','C'] }
const result = ref(null)        // 提交后的成绩
const saveSelection = ref([])   // 结果页勾选要入库的题 id
const displayScore = ref(0)     // 结果页分数滚动计数（gsap 驱动）

let pollTimer = null
let activeGen = 0   // 生成令牌：取消/重开时自增，作废所有在途请求结果

const typeLabel = { single: '单选', multiple: '多选', judge: '判断' }

function resetAnswers(qs) {
  Object.keys(answers).forEach((k) => delete answers[k])
  qs.forEach((q) => { answers[q.id] = q.questionType === 'multiple' ? [] : '' })
}

// ============ AI 出题（异步轮询） ============
async function startGenerate() {
  generating.value = true
  genError.value = ''
  const myGen = ++activeGen
  try {
    const res = await generateQuiz()
    if (myGen !== activeGen) return        // 期间已取消，丢弃结果
    sessionId.value = res.data
    pollSession(myGen)
  } catch (e) {
    if (myGen !== activeGen) return
    generating.value = false
    genError.value = e.message || '出题失败'
    ElMessage.error(genError.value)
  }
}

function pollSession(myGen) {
  clearPoll()
  pollTimer = setInterval(async () => {
    try {
      const res = await getQuizSession(sessionId.value)
      if (myGen !== activeGen) return      // 已取消，丢弃在途结果
      const d = res.data
      if (d.status === 'READY') {
        clearPoll()
        generating.value = false
        loadSession(d)
      } else if (d.status === 'FAILED') {
        clearPoll()
        generating.value = false
        genError.value = d.errorMsg || '生成失败，知识库内容可能不足'
        ElMessage.warning(genError.value)
      }
    } catch (e) {
      if (myGen !== activeGen) return
      clearPoll()
      generating.value = false
      genError.value = e.message || '查询会话失败'
    }
  }, 3000)
}

// 取消出题：作废当前生成令牌，停止轮询并复位（后端异步任务由它自行结束，前端不再等待）
function cancelGenerate() {
  if (!generating.value) return
  activeGen++
  clearPoll()
  generating.value = false
  sessionId.value = null
  genError.value = ''
  ElMessage.info('已取消出题')
}

function clearPoll() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

function loadSession(d) {
  sessionId.value = d.sessionId || sessionId.value
  mode.value = d.mode
  questions.value = d.questions || []
  resetAnswers(questions.value)
  result.value = null
  saveSelection.value = []
  view.value = 'answering'
}

// ============ 题库练习（同步） ============
async function startPractice() {
  loading.value = true
  try {
    const res = await practiceQuiz(5)
    const d = res.data
    sessionId.value = d.sessionId
    mode.value = 'BANK_PRACTICE'
    questions.value = d.questions || []
    resetAnswers(questions.value)
    result.value = null
    saveSelection.value = []
    view.value = 'answering'
  } catch (e) {
    ElMessage.error(e.message || '题库练习失败')
  } finally {
    loading.value = false
  }
}

// ============ 提交判分 ============
const answeredCount = computed(() =>
  questions.value.filter((q) => {
    const a = answers[q.id]
    return Array.isArray(a) ? a.length > 0 : !!a
  }).length
)

async function submit() {
  const payload = {}
  questions.value.forEach((q) => {
    const a = answers[q.id]
    payload[q.id] = Array.isArray(a) ? a.slice().sort().join(',') : a
  })
  loading.value = true
  try {
    const res = await submitQuiz(sessionId.value, payload)
    result.value = res.data
    view.value = 'result'
  } catch (e) {
    ElMessage.error(e.message || '提交失败')
  } finally {
    loading.value = false
  }
}

// ============ 入库 ============
async function saveSelected() {
  if (!saveSelection.value.length) { ElMessage.info('请先勾选要收藏的题目'); return }
  loading.value = true
  try {
    const res = await saveQuizToBank(sessionId.value, saveSelection.value)
    ElMessage.success(`已收藏 ${res.data} 道题到个人题库`)
    saveSelection.value = []
  } catch (e) {
    ElMessage.error(e.message || '收藏失败')
  } finally {
    loading.value = false
  }
}

// ============ 我的题库 / 掌握度 ============
const bankVisible = ref(false)
const bankList = ref([])
async function openBank() {
  bankVisible.value = true
  try { bankList.value = (await listQuizBank()).data || [] } catch { bankList.value = [] }
}

const masteryVisible = ref(false)
const masteryList = ref([])
async function openMastery() {
  masteryVisible.value = true
  try { masteryList.value = (await listQuizMastery()).data || [] } catch { masteryList.value = [] }
}
function ratePercent(m) {
  if (m.correctRate === null || m.correctRate === undefined) return null
  return Math.round(m.correctRate * 100)
}

function backHome() {
  clearPoll()
  view.value = 'home'
  questions.value = []
  result.value = null
}

const scoreColor = computed(() => {
  if (!result.value) return ''
  const r = result.value.score / (result.value.total || 1)
  return r >= 0.8 ? 'var(--plaza-success)' : r >= 0.5 ? '#d48806' : '#cf1322'
})

// ============ 生成中防离开：强制停留（路由）+ 刷新/关闭兜底 ============
// 路由跳转守卫：生成中拦截，二次确认；选择留下则取消导航，选择离开则先取消出题。
onBeforeRouteLeave(async () => {
  if (!generating.value) return true
  try {
    await ElMessageBox.confirm(
      'AI 正在生成题目，离开本页将取消本次出题。确定要离开吗？',
      '正在出题',
      { confirmButtonText: '取消出题并离开', cancelButtonText: '留在本页', type: 'warning' }
    )
    cancelGenerate()
    return true
  } catch {
    return false   // 留在本页
  }
})

// 浏览器级兜底：生成中刷新/关闭标签页时弹原生确认
function onBeforeUnload(e) {
  if (!generating.value) return
  e.preventDefault()
  e.returnValue = ''
}

// ============================ GSAP 动画 ============================
// Vue 最佳实践：onMounted 里建 gsap.context(scope)，按视图注册入场动画，
// onUnmounted 里 ctx.revert() 统一清理；尊重 prefers-reduced-motion。
const root = ref(null)
let ctx = null
let genTween = null
const reduce = typeof window !== 'undefined'
  && window.matchMedia
  && window.matchMedia('(prefers-reduced-motion: reduce)').matches

onMounted(() => {
  if (!root.value) return
  ctx = gsap.context((self) => {
    // 首页：hero 文案 + 模式卡 stagger 入场
    self.add('enterHome', () => {
      if (reduce) return
      gsap.from('.js-hero-item', { autoAlpha: 0, y: 16, duration: 0.5, stagger: 0.07, ease: 'power2.out' })
      gsap.from('.js-card', { autoAlpha: 0, y: 26, scale: 0.97, duration: 0.6, stagger: 0.12, ease: 'power3.out', delay: 0.08, clearProps: 'transform' })
    })
    // 答题页：题卡逐张滑入
    self.add('enterAnswer', () => {
      if (reduce) return
      gsap.from('.js-answer-bar', { autoAlpha: 0, y: 12, duration: 0.4, ease: 'power2.out' })
      gsap.from('.js-q', { autoAlpha: 0, y: 22, duration: 0.5, stagger: 0.08, ease: 'power2.out', delay: 0.05, clearProps: 'transform' })
    })
    // 结果页：分数计数 + 成绩卡弹入 + 逐题 stagger，答错卡抖动强调
    self.add('enterResult', () => {
      const score = result.value ? result.value.score : 0
      if (reduce) { displayScore.value = score; return }
      gsap.from('.js-score', { autoAlpha: 0, scale: 0.88, duration: 0.55, ease: 'back.out(1.7)' })
      // 分数滚动计数
      const proxy = { v: 0 }
      displayScore.value = 0
      gsap.to(proxy, {
        v: score, duration: 0.9, ease: 'power1.out', delay: 0.15,
        onUpdate: () => { displayScore.value = Math.round(proxy.v) },
      })
      gsap.from('.js-rq', {
        autoAlpha: 0, y: 22, duration: 0.5, stagger: 0.09, ease: 'power2.out', delay: 0.2,
        clearProps: 'transform',
        onComplete: () => {
          // 答错的题轻微抖一下，强调
          gsap.utils.toArray('.js-rq.bad').forEach((el) => {
            gsap.fromTo(el, { x: 0 },
              { x: 6, duration: 0.07, repeat: 5, yoyo: true, ease: 'power1.inOut', clearProps: 'x' })
          })
        },
      })
    })
    // 生成中：AI 图标脉冲循环
    self.add('startGen', () => {
      if (reduce) return
      genTween = gsap.to('.js-ai-icon', { scale: 1.12, duration: 0.6, repeat: -1, yoyo: true, ease: 'sine.inOut' })
    })
    self.add('stopGen', () => {
      if (genTween) { genTween.kill(); genTween = null }
      gsap.set('.js-ai-icon', { scale: 1 })
    })
    // 取消按钮入场：随生成态出现轻微滑入
    self.add('showCancel', () => {
      if (reduce) return
      gsap.from('.js-cancel', { autoAlpha: 0, y: 8, duration: 0.4, ease: 'power2.out' })
    })
  }, root.value)

  // 首屏进入
  ctx.enterHome()
  window.addEventListener('beforeunload', onBeforeUnload)
})

onUnmounted(() => {
  clearPoll()
  window.removeEventListener('beforeunload', onBeforeUnload)
  ctx && ctx.revert()
})

// 视图切换 → 触发对应入场动画（等 DOM 更新后）
watch(view, (v) => {
  nextTick(() => {
    if (!ctx) return
    if (v === 'home') ctx.enterHome()
    else if (v === 'answering') ctx.enterAnswer()
    else if (v === 'result') ctx.enterResult()
  })
})

// 生成态 → 脉冲动画开关 + 取消按钮入场
watch(generating, (g) => {
  if (!ctx) return
  if (g) {
    ctx.startGen()
    nextTick(() => ctx.showCancel())
  } else {
    ctx.stopGen()
  }
})
</script>

<template>
  <div class="quiz-wrap" ref="root">
    <!-- ============ 入口 ============ -->
    <template v-if="view === 'home'">
      <div class="hero">
        <div class="hero-text">
          <h1 class="js-hero-item">知识问答</h1>
          <p class="js-hero-item">根据你的个人画像弱点出题，边练边补，逐步提升对应知识水平。</p>
        </div>
        <div class="hero-actions js-hero-item">
          <button class="ghost-btn" @click="openMastery">掌握度档案</button>
          <button class="ghost-btn" @click="openBank">我的题库</button>
        </div>
      </div>

      <div v-if="genError" class="gen-error">
        <el-alert :title="genError" type="warning" :closable="false" show-icon />
      </div>

      <div class="cards">
        <div class="mode-card js-card" :class="{ busy: generating }">
          <div class="mc-icon ai js-ai-icon">AI</div>
          <h3>AI 出题</h3>
          <p>实时按你的画像弱点 + 知识库证据生成可溯源客观题（无源不出题）。</p>
          <el-button :loading="generating" round @click="startGenerate">
            {{ generating ? '正在生成题目…' : '来一套题' }}
          </el-button>
          <template v-if="generating">
            <span class="mc-hint">检索知识库 + LLM 出题，约需十几秒</span>
            <button class="cancel-btn js-cancel" @click="cancelGenerate">
              <span class="cx">✕</span> 取消出题
            </button>
            <span class="stay-hint">生成期间请勿离开本页，否则出题将中断</span>
          </template>
        </div>

        <div class="mode-card js-card">
          <div class="mc-icon bank">库</div>
          <h3>题库练习</h3>
          <p>从你收藏的个人题库中弱点优先抽题复习，巩固薄弱环节。</p>
          <el-button :loading="loading" round @click="startPractice">开始练习</el-button>
        </div>
      </div>
    </template>

    <!-- ============ 答题 ============ -->
    <template v-else-if="view === 'answering'">
      <div class="bar js-answer-bar">
        <button class="link-btn" @click="backHome">← 返回</button>
        <span class="bar-title">{{ mode === 'BANK_PRACTICE' ? '题库练习' : 'AI 出题' }}</span>
        <span class="bar-progress">已答 {{ answeredCount }} / {{ questions.length }}</span>
      </div>

      <div v-for="(q, i) in questions" :key="q.id" class="q-card js-q">
        <div class="q-head">
          <span class="q-idx">{{ i + 1 }}</span>
          <el-tag size="small" effect="plain" class="q-type">{{ typeLabel[q.questionType] || q.questionType }}</el-tag>
          <el-tag size="small" type="info" effect="plain" class="q-topic">{{ q.topic }}</el-tag>
        </div>
        <div class="q-stem">{{ q.stem }}</div>

        <!-- 单选 / 判断 -->
        <el-radio-group v-if="q.questionType !== 'multiple'" v-model="answers[q.id]" class="q-opts">
          <el-radio v-for="op in q.options" :key="op.key" :value="op.key" class="q-opt" border>
            <b>{{ op.key }}.</b> {{ op.text }}
          </el-radio>
        </el-radio-group>

        <!-- 多选 -->
        <el-checkbox-group v-else v-model="answers[q.id]" class="q-opts">
          <el-checkbox v-for="op in q.options" :key="op.key" :value="op.key" class="q-opt" border>
            <b>{{ op.key }}.</b> {{ op.text }}
          </el-checkbox>
        </el-checkbox-group>
      </div>

      <div class="submit-bar">
        <el-button type="primary" size="large" round :loading="loading"
                   :disabled="answeredCount === 0" @click="submit">
          提交答案
        </el-button>
      </div>
    </template>

    <!-- ============ 成绩 / 解析 ============ -->
    <template v-else-if="view === 'result' && result">
      <div class="bar">
        <button class="link-btn" @click="backHome">← 返回</button>
        <span class="bar-title">本次成绩</span>
      </div>

      <div class="score-card js-score">
        <div class="score-num" :style="{ color: scoreColor }">
          {{ displayScore }}<span class="score-total">/ {{ result.total }}</span>
        </div>
        <div class="score-label">答对题数</div>
      </div>

      <div v-for="(q, i) in result.questions" :key="q.id" class="q-card result js-rq"
           :class="{ ok: q.isCorrect === 1, bad: q.isCorrect === 0 }">
        <div class="q-head">
          <span class="q-idx">{{ i + 1 }}</span>
          <el-tag size="small" effect="plain" class="q-type">{{ typeLabel[q.questionType] || q.questionType }}</el-tag>
          <el-tag size="small" type="info" effect="plain" class="q-topic">{{ q.topic }}</el-tag>
          <span class="judge" :class="q.isCorrect === 1 ? 'right' : 'wrong'">
            {{ q.isCorrect === 1 ? '✓ 答对' : '✗ 答错' }}
          </span>
          <el-checkbox v-if="mode !== 'BANK_PRACTICE'" :value="q.id"
                       :model-value="saveSelection.includes(q.id)"
                       @change="(v) => v ? saveSelection.push(q.id) : (saveSelection = saveSelection.filter(x => x !== q.id))"
                       class="q-save">收藏</el-checkbox>
        </div>
        <div class="q-stem">{{ q.stem }}</div>

        <div class="opt-review">
          <div v-for="op in q.options" :key="op.key" class="rev-opt"
               :class="{
                 correct: (q.correctAnswer || '').split(',').includes(op.key),
                 chosen: (q.workerAnswer || '').split(',').includes(op.key),
               }">
            <b>{{ op.key }}.</b> {{ op.text }}
            <span v-if="(q.correctAnswer || '').split(',').includes(op.key)" class="mk correct-mk">正确答案</span>
            <span v-else-if="(q.workerAnswer || '').split(',').includes(op.key)" class="mk chosen-mk">你的选择</span>
          </div>
        </div>

        <div v-if="q.explanation" class="explain">
          <span class="explain-tag">解析</span>{{ q.explanation }}
        </div>
        <div v-if="q.sources && q.sources.length" class="sources">
          <span class="src-tag">来源</span>
          <el-tag v-for="(s, si) in q.sources" :key="si" size="small" effect="plain" class="src">
            {{ s.type || s.documentId || '知识库' }}
          </el-tag>
        </div>
      </div>

      <div v-if="mode !== 'BANK_PRACTICE'" class="submit-bar">
        <el-button type="primary" round :loading="loading"
                   :disabled="!saveSelection.length" @click="saveSelected">
          收藏选中的 {{ saveSelection.length }} 道题到个人题库
        </el-button>
        <el-button round @click="backHome">完成</el-button>
      </div>
      <div v-else class="submit-bar">
        <el-button round @click="backHome">完成</el-button>
      </div>
    </template>

    <!-- ============ 我的题库 ============ -->
    <el-dialog v-model="bankVisible" title="我的题库" width="640px">
      <el-empty v-if="!bankList.length" description="题库还是空的，去「AI 出题」攒几道好题吧" />
      <div v-else class="bank-list">
        <div v-for="b in bankList" :key="b.id" class="bank-item">
          <el-tag size="small" type="info" effect="plain">{{ b.topic }}</el-tag>
          <el-tag size="small" effect="plain">{{ typeLabel[b.questionType] || b.questionType }}</el-tag>
          <span class="bank-stem">{{ b.stem }}</span>
        </div>
      </div>
    </el-dialog>

    <!-- ============ 掌握度 ============ -->
    <el-dialog v-model="masteryVisible" title="掌握度档案" width="560px">
      <el-empty v-if="!masteryList.length" description="还没有答题记录，先来一套题吧" />
      <div v-else class="mastery-list">
        <div v-for="m in masteryList" :key="m.topic" class="mastery-item">
          <div class="m-top">
            <span class="m-topic">{{ m.topic }}</span>
            <span class="m-rate">{{ ratePercent(m) === null ? '—' : ratePercent(m) + '%' }}</span>
          </div>
          <el-progress :percentage="ratePercent(m) || 0" :stroke-width="8" :show-text="false"
                       :color="ratePercent(m) >= 80 ? 'var(--plaza-success)' : ratePercent(m) >= 50 ? '#d48806' : '#cf1322'" />
          <span class="m-count">共答 {{ m.totalCount }} 题</span>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.quiz-wrap { max-width: 880px; margin: 0 auto; }

/* hero */
.hero {
  display: flex; align-items: flex-end; justify-content: space-between;
  gap: 16px; margin-bottom: 26px;
}
.hero-text h1 {
  font-family: var(--font-display, inherit); font-size: 28px; font-weight: 800;
  color: var(--plaza-heading, #1a1a1a); margin: 0 0 6px;
}
.hero-text p { color: var(--plaza-text-muted, #666); font-size: 14px; margin: 0; max-width: 520px; }
.hero-actions { display: flex; gap: 10px; flex-shrink: 0; }
.ghost-btn {
  height: 36px; padding: 0 16px; border: 1px solid var(--plaza-border, #ddd);
  border-radius: 999px; background: var(--plaza-bg-card, #fff); color: var(--plaza-text, #333);
  font-size: 13px; cursor: pointer; transition: all .18s;
}
.ghost-btn:hover { border-color: var(--signal, #ff8a00); color: var(--signal, #ff8a00); }

.gen-error { margin-bottom: 18px; }

/* mode cards */
.cards { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
.mode-card {
  background: var(--plaza-bg-card, #fff); border: 1px solid var(--plaza-border, #e5e5e5);
  border-radius: 16px; padding: 28px 24px; display: flex; flex-direction: column;
  align-items: flex-start; gap: 10px; transition: box-shadow .2s, transform .2s;
}
.mode-card:hover { box-shadow: 0 8px 30px rgba(0,0,0,.08); transform: translateY(-2px); }
.mode-card.busy { box-shadow: 0 0 0 2px var(--signal-soft, rgba(255,138,0,.15)); }
.mc-icon {
  width: 46px; height: 46px; border-radius: 12px; display: grid; place-items: center;
  font-weight: 800; font-size: 17px; color: #fff;
}
.mc-icon.ai { background: linear-gradient(150deg, #ef9244, #db6f2a); }
.mc-icon.bank { background: linear-gradient(150deg, #e0982f, #b9791b); }
.mode-card h3 { margin: 4px 0 0; font-size: 18px; color: var(--plaza-heading, #1a1a1a); }
.mode-card p { margin: 0 0 8px; font-size: 13px; color: var(--plaza-text-muted, #777); line-height: 1.6; }
.mode-card > .el-button { margin-top: auto; }
.mc-hint { font-size: 12px; color: var(--plaza-text-muted, #999); }

/* 取消出题：贴合暖陶色，次要操作，hover 转暖红警示 */
.cancel-btn {
  margin-top: 6px;
  align-self: flex-start;
  display: inline-flex; align-items: center; gap: 6px;
  height: 32px; padding: 0 16px;
  border: 1px solid var(--plaza-border, #e0d3bf);
  border-radius: 999px;
  background: var(--plaza-bg-card, #fff);
  color: var(--plaza-text-muted, #8a7d6c);
  font-size: 13px; font-weight: 600; letter-spacing: .2px;
  cursor: pointer;
  transition: border-color .18s ease, color .18s ease, background .18s ease, transform .12s ease;
}
.cancel-btn:hover {
  border-color: #cf5a3c; color: #cf5a3c; background: rgba(207, 90, 60, .06);
}
.cancel-btn:active { transform: scale(.97); }
.cancel-btn .cx { font-size: 12px; line-height: 1; }
.stay-hint {
  margin-top: 2px; font-size: 11.5px; color: #c08a4a;
  display: inline-flex; align-items: center;
}

/* bar */
.bar { display: flex; align-items: center; gap: 14px; margin-bottom: 18px; }
.bar-title { font-size: 18px; font-weight: 700; color: var(--plaza-heading, #1a1a1a); }
.bar-progress { margin-left: auto; font-size: 13px; color: var(--plaza-text-muted, #777); }
.link-btn, .submit-bar .link-btn {
  border: none; background: none; color: var(--signal, #ff8a00); cursor: pointer; font-size: 14px;
}

/* question card */
.q-card {
  background: var(--plaza-bg-card, #fff); border: 1px solid var(--plaza-border, #e5e5e5);
  border-radius: 14px; padding: 20px 22px; margin-bottom: 16px;
}
.q-card.result.ok { border-left: 4px solid var(--plaza-success, #16a34a); }
.q-card.result.bad { border-left: 4px solid #cf1322; }
.q-head { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
.q-idx {
  width: 24px; height: 24px; border-radius: 7px; background: var(--signal-soft, rgba(255,138,0,.12));
  color: var(--signal, #ff8a00); font-weight: 700; font-size: 13px; display: grid; place-items: center;
}
.q-topic { max-width: 240px; }
.q-save { margin-left: auto; }
.judge { margin-left: auto; font-weight: 700; font-size: 13px; }
.judge.right { color: var(--plaza-success, #16a34a); }
.judge.wrong { color: #cf1322; }
.q-stem { font-size: 15px; line-height: 1.7; color: var(--plaza-text, #222); margin-bottom: 14px; font-weight: 500; }
.q-opts { display: flex; flex-direction: column; gap: 10px; width: 100%; }
.q-opt { width: 100%; margin: 0 !important; height: auto !important; padding: 10px 14px !important; white-space: normal !important; }
:deep(.q-opt .el-radio__label), :deep(.q-opt .el-checkbox__label) { white-space: normal; line-height: 1.5; }

/* result option review */
.opt-review { display: flex; flex-direction: column; gap: 8px; }
.rev-opt {
  padding: 9px 13px; border: 1px solid var(--plaza-border, #e5e5e5); border-radius: 9px;
  font-size: 14px; color: var(--plaza-text, #333); position: relative;
}
.rev-opt.correct { border-color: var(--plaza-success, #16a34a); background: rgba(22,163,74,.06); }
.rev-opt.chosen:not(.correct) { border-color: #cf1322; background: rgba(207,19,34,.05); }
.mk { float: right; font-size: 12px; font-weight: 700; }
.correct-mk { color: var(--plaza-success, #16a34a); }
.chosen-mk { color: #cf1322; }
.explain {
  margin-top: 14px; padding: 12px 14px; background: var(--plaza-bg, #f7f8fa); border-radius: 9px;
  font-size: 13.5px; line-height: 1.7; color: var(--plaza-text, #444);
}
.explain-tag, .src-tag {
  display: inline-block; font-size: 11px; font-weight: 700; color: var(--signal, #ff8a00);
  background: var(--signal-soft, rgba(255,138,0,.12)); padding: 1px 7px; border-radius: 5px; margin-right: 8px;
}
.sources { margin-top: 10px; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }

/* score */
.score-card {
  background: var(--plaza-bg-card, #fff); border: 1px solid var(--plaza-border, #e5e5e5);
  border-radius: 16px; padding: 26px; text-align: center; margin-bottom: 20px;
}
.score-num { font-size: 46px; font-weight: 800; font-family: var(--font-mono, monospace); }
.score-total { font-size: 22px; color: var(--plaza-text-muted, #999); margin-left: 4px; }
.score-label { font-size: 13px; color: var(--plaza-text-muted, #777); margin-top: 4px; }

.submit-bar { display: flex; justify-content: center; gap: 12px; margin: 24px 0 10px; }

/* dialogs */
.bank-list, .mastery-list { display: flex; flex-direction: column; gap: 12px; max-height: 60vh; overflow-y: auto; }
.bank-item { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border: 1px solid var(--plaza-border, #eee); border-radius: 9px; }
.bank-stem { font-size: 13.5px; color: var(--plaza-text, #333); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mastery-item { padding: 10px 4px; }
.m-top { display: flex; justify-content: space-between; margin-bottom: 6px; }
.m-topic { font-size: 14px; font-weight: 600; color: var(--plaza-text, #333); }
.m-rate { font-size: 14px; font-weight: 700; font-family: var(--font-mono, monospace); }
.m-count { font-size: 12px; color: var(--plaza-text-muted, #999); }

@media (max-width: 720px) {
  .cards { grid-template-columns: 1fr; }
  .hero { flex-direction: column; align-items: flex-start; }
}
</style>
