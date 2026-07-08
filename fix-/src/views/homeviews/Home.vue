<script setup>
import { useRouter } from 'vue-router'
import {
  DocumentChecked,
  EditPen,
  Connection,
  Check,
  ArrowRight,
} from '@element-plus/icons-vue'

const router = useRouter()

function goToLogin() {
  router.push('/login')
}

const features = [
  {
    icon: DocumentChecked,
    title: '作业指引',
    desc: '标准化流程指引，步骤化操作指导，降低操作失误率',
  },
  {
    icon: EditPen,
    title: '知识沉淀',
    desc: '检修案例实时上传审核，知识图谱自动更新与优化',
  },
  {
    icon: Connection,
    title: '结果校正',
    desc: '人工标注修正大模型输出结果，持续优化系统适配性',
  },
]

const steps = [
  {
    num: '01',
    title: '输入检索条件',
    desc: '输入设备型号、故障描述或上传图片',
  },
  {
    num: '02',
    title: '智能匹配',
    desc: '系统基于多模态大模型进行语义理解和跨模态匹配',
  },
  {
    num: '03',
    title: '获取检修方案',
    desc: '快速获取精准的检修知识与标准作业流程',
  },
  {
    num: '04',
    title: '作业执行与反馈',
    desc: '按照指引执行作业，支持结果校正与知识贡献',
  },
]
</script>

<template>
  <div class="landing">
    <!-- Navigation -->
    <nav class="landing-nav">
      <div class="landing-nav-inner">
        <div class="landing-logo">
          <div class="brand-logo-icon">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
              <path d="M22.7 19l-9.1-9.1c.9-2.3.4-5-1.5-6.9-2-2-5-2.4-7.4-1.3L9 6 6 9 1.6 4.7C.4 7.1.9 10.1 2.9 12.1c1.9 1.9 4.6 2.4 6.9 1.5l9.1 9.1c.4.4 1 .4 1.4 0l2.3-2.3c.5-.4.5-1.1.1-1.4z"/>
            </svg>
          </div>
          <span class="logo-text">检修系统</span>
        </div>
        <div class="landing-nav-actions">
          <el-button type="primary" @click="goToLogin">免费开始</el-button>
        </div>
      </div>
    </nav>

    <!-- Hero Section -->
    <section class="hero">
      <div class="hero-bg-grid"></div>
      <div class="hero-bg-circles">
        <div class="bg-circle circle-1"></div>
        <div class="bg-circle circle-2"></div>
        <div class="bg-circle circle-3"></div>
      </div>
      <div class="hero-content">
        <div class="hero-badge">
          <span class="badge-dot"></span>
          智能设备检修平台
        </div>
        <h1 class="hero-title">
          让智能检修<br />
          <span class="highlight">成为你的伙伴</span>
        </h1>
        <p class="hero-subtitle">
          设备检修知识检索系统，基于多模态大模型技术，融合文本、图像、设备型号等多模态信息，
          <br />实现精准语义检索与跨模态匹配，为工业设备检修提供智能化解决方案。
        </p>
      </div>
    </section>

    <!-- Features Section -->
    <section class="features">
      <div class="section-header">
        <span class="section-tag">核心功能</span>
        <h2 class="section-title">一站式智能检修解决方案</h2>
        <p class="section-desc">从知识检索到作业执行，提供完整的智能检修工具链</p>
      </div>
      <div class="features-grid">
        <div
          v-for="(feature, index) in features"
          :key="index"
          class="feature-card"
          :style="{ animationDelay: `${index * 100}ms` }"
        >
          <div class="feature-icon-wrap">
            <el-icon class="feature-icon"><component :is="feature.icon" /></el-icon>
          </div>
          <h3 class="feature-title">{{ feature.title }}</h3>
          <p class="feature-desc">{{ feature.desc }}</p>
        </div>
      </div>
    </section>

    <!-- How It Works Section -->
    <section class="how-it-works">
      <div class="section-header">
        <span class="section-tag">使用流程</span>
        <h2 class="section-title">简单四步，快速上手</h2>
        <p class="section-desc">无论是知识检索还是作业执行，流程清晰明了</p>
      </div>
      <div class="steps">
        <div
          v-for="(step, index) in steps"
          :key="index"
          class="step-item"
          :style="{ animationDelay: `${index * 120}ms` }"
        >
          <div class="step-num">{{ step.num }}</div>
          <div class="step-content">
            <h3 class="step-title">{{ step.title }}</h3>
            <p class="step-desc">{{ step.desc }}</p>
          </div>
          <div v-if="index < steps.length - 1" class="step-arrow">
            <el-icon><ArrowRight /></el-icon>
          </div>
        </div>
      </div>
    </section>

    <!-- CTA Section -->
    <section class="cta">
      <div class="cta-content">
        <h2 class="cta-title">准备好提升检修效率了吗？</h2>
        <p class="cta-desc">免费注册，即刻开启智能检修之旅</p>
        <div class="cta-actions">
          <el-button type="primary" size="large" class="cta-btn" @click="goToLogin">
            立即免费开始
            <el-icon class="btn-icon"><ArrowRight /></el-icon>
          </el-button>
        </div>
        <div class="cta-features">
          <span class="cta-feature-item">
            <el-icon class="check-icon"><Check /></el-icon>
            无需信用卡
          </span>
          <span class="cta-feature-item">
            <el-icon class="check-icon"><Check /></el-icon>
            永久免费基础版
          </span>
          <span class="cta-feature-item">
            <el-icon class="check-icon"><Check /></el-icon>
            随时可取消
          </span>
        </div>
      </div>
    </section>

    <!-- Footer -->
    <footer class="landing-footer">
      <div class="footer-content">
        <div class="footer-brand">
          <div class="landing-logo">
            <span class="logo-icon">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor">
              <path d="M22.7 19l-9.1-9.1c.9-2.3.4-5-1.5-6.9-2-2-5-2.4-7.4-1.3L9 6 6 9 1.6 4.7C.4 7.1.9 10.1 2.9 12.1c1.9 1.9 4.6 2.4 6.9 1.5l9.1 9.1c.4.4 1 .4 1.4 0l2.3-2.3c.5-.4.5-1.1.1-1.4z"/>
            </svg>
          </span>
            <span class="logo-text">检修系统</span>
          </div>
          <p class="footer-tagline">让智能检修成为你的伙伴</p>
        </div>
        <div class="footer-links">
          <div class="footer-link-group">
            <span class="footer-link-title">产品</span>
            <a href="#" class="footer-link">功能介绍</a>
            <a href="#" class="footer-link">使用文档</a>
            <a href="#" class="footer-link">更新日志</a>
          </div>
          <div class="footer-link-group">
            <span class="footer-link-title">支持</span>
            <a href="#" class="footer-link">常见问题</a>
            <a href="#" class="footer-link">联系我们</a>
            <a href="#" class="footer-link">隐私政策</a>
          </div>
        </div>
      </div>
      <div class="footer-bottom">
        <span>© 2024 设备检修知识检索系统. All rights reserved.</span>
      </div>
    </footer>
  </div>
</template>

<style scoped>
.landing {
  min-height: 100vh;
  background: var(--plaza-bg);
}

/* Navigation */
.landing-nav {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 100;
  background: rgba(248, 250, 252, 0.9);
  backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--plaza-border);
}
.landing-nav-inner {
  max-width: 1200px;
  margin: 0 auto;
  padding: 16px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.landing-logo {
  display: flex;
  align-items: center;
  gap: 4px;
  font-weight: 700;
  font-size: 20px;
  letter-spacing: 0.02em;
}
.logo-icon {
  background: var(--plaza-accent);
  color: #fff;
  padding: 4px 8px;
  border-radius: 6px;
  font-size: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.brand-logo-icon {
  position: relative;
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--plaza-accent);
  border-radius: 50%;
  flex-shrink: 0;
}

.brand-logo-icon::before {
  content: '';
  position: absolute;
  width: 100%;
  height: 100%;
  border: 3px solid rgba(255, 255, 255, 0.3);
  border-radius: 50%;
  animation: pulse-ring 2s ease-in-out infinite;
}

.brand-logo-icon svg {
  color: #fff;
  width: 16px;
  height: 16px;
  filter: drop-shadow(0 0 4px var(--plaza-accent-soft-strong));
}

@keyframes pulse-ring {
  0%, 100% {
    transform: scale(1);
    opacity: 1;
  }
  50% {
    transform: scale(1.3);
    opacity: 0;
  }
}
.logo-text {
  color: var(--plaza-text);
}
.landing-nav-actions {
  display: flex;
  gap: 12px;
}
.landing-nav-actions .el-button--primary {
  background: var(--plaza-accent) !important;
  border-color: var(--plaza-accent) !important;
  color: #fff !important;
  font-weight: 600;
}
.landing-nav-actions .el-button--primary:hover {
  background: var(--plaza-accent-hover) !important;
  border-color: var(--plaza-accent-hover) !important;
}

/* Hero */
.hero {
  position: relative;
  padding: clamp(80px, 15vw, 160px) 5vw clamp(50px, 10vw, 100px);
  text-align: center;
  overflow: hidden;
}
.hero-bg-grid {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(var(--plaza-decor-line) 1px, transparent 1px),
    linear-gradient(90deg, var(--plaza-decor-line) 1px, transparent 1px);
  background-size: 48px 48px;
  pointer-events: none;
}
.hero-bg-circles {
  position: absolute;
  inset: 0;
  pointer-events: none;
  overflow: hidden;
}
.bg-circle {
  position: absolute;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--plaza-decor-wash) 0%, transparent 60%);
}
.circle-1 {
  width: 500px;
  height: 500px;
  top: -150px;
  right: -100px;
}
.circle-2 {
  width: 400px;
  height: 400px;
  bottom: -100px;
  left: -100px;
}
.circle-3 {
  width: 300px;
  height: 300px;
  top: 40%;
  left: 10%;
  background: linear-gradient(135deg, var(--plaza-decor-wash-soft) 0%, transparent 60%);
}
.hero-content {
  position: relative;
  max-width: 800px;
  margin: 0 auto;
  animation: fadeInUp 0.8s ease;
}
.hero-badge {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  background: var(--plaza-accent-soft);
  border: 1px solid rgba(132, 204, 22, 0.2);
  border-radius: 20px;
  font-size: 13px;
  font-weight: 500;
  color: var(--plaza-text);
  margin-bottom: 24px;
}
.badge-dot {
  width: 8px;
  height: 8px;
  background: var(--plaza-accent);
  border-radius: 50%;
  animation: pulse 2s infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.6; transform: scale(1.2); }
}
.hero-title {
  font-size: clamp(1.8rem, 5vw, 3.5rem);
  font-weight: 700;
  color: var(--plaza-text);
  line-height: 1.2;
  margin-bottom: 1.2rem;
  letter-spacing: -0.02em;
}
.highlight {
  color: var(--plaza-accent);
}
.hero-subtitle {
  font-size: 1.2rem;
  color: var(--plaza-text-muted);
  line-height: 1.7;
  margin-bottom: 36px;
}
.btn-icon {
  margin-left: 8px;
}

/* Sections */
.section-header {
  text-align: center;
  margin-bottom: clamp(30px, 5vw, 60px);
}
.section-tag {
  display: inline-block;
  padding: 0.4rem 0.9rem;
  background: var(--plaza-accent-soft);
  border-radius: 20px;
  font-size: clamp(10px, 1.2vw, 12px);
  font-weight: 600;
  color: var(--plaza-accent);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 1rem;
}
.section-title {
  font-size: clamp(1.5rem, 4vw, 2.5rem);
  font-weight: 700;
  color: var(--plaza-text);
  margin-bottom: 0.8rem;
}
.section-desc {
  font-size: clamp(0.9rem, 1.8vw, 1.1rem);
  color: var(--plaza-text-muted);
}

/* Features */
.features {
  padding: clamp(50px, 8vw, 100px) 5vw;
  background: var(--plaza-bg-card);
}
.features-grid {
  max-width: 1200px;
  margin: 0 auto;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: clamp(15px, 2vw, 24px);
}
.feature-card {
  padding: clamp(1.2rem, 3vw, 32px) clamp(1rem, 2vw, 24px);
  background: var(--plaza-bg);
  border: 1px solid var(--plaza-border);
  border-radius: 1rem;
  transition: transform 0.3s ease, box-shadow 0.3s ease, border-color 0.3s ease;
  animation: fadeInUp 0.6s ease both;
}
.feature-card:hover {
  transform: translateY(-4px);
  box-shadow: var(--plaza-shadow-organic-hover);
  border-color: var(--plaza-accent);
}
.feature-icon-wrap {
  width: clamp(40px, 7vw, 56px);
  height: clamp(40px, 7vw, 56px);
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--plaza-accent-soft);
  border-radius: 0.8rem;
  margin-bottom: 1.2rem;
}
.feature-icon {
  font-size: clamp(20px, 3vw, 28px);
  color: var(--plaza-accent);
}
.feature-title {
  font-size: clamp(1rem, 2vw, 1.2rem);
  font-weight: 600;
  color: var(--plaza-text);
  margin-bottom: 0.8rem;
}
.feature-desc {
  font-size: clamp(12px, 1.5vw, 14px);
  color: var(--plaza-text-muted);
  line-height: 1.6;
}

/* How It Works */
.how-it-works {
  padding: clamp(50px, 8vw, 100px) 5vw;
}
.steps {
  max-width: 1000px;
  margin: 0 auto;
  display: flex;
  align-items: flex-start;
  gap: clamp(10px, 2vw, 20px);
}
.step-item {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  position: relative;
  animation: fadeInUp 0.6s ease both;
}
.step-num {
  width: clamp(48px, 8vw, 64px);
  height: clamp(48px, 8vw, 64px);
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--step-num-bg);
  color: var(--step-num-text);
  font-size: clamp(1rem, 2vw, 1.2rem);
  font-weight: 700;
  font-family: var(--font-mono);
  border-radius: 50%;
  margin-bottom: 1rem;
}
.step-content {
  padding: 0 0.5rem;
}
.step-title {
  font-size: clamp(0.9rem, 1.8vw, 1.1rem);
  font-weight: 600;
  color: var(--plaza-text);
  margin-bottom: 0.5rem;
}
.step-desc {
  font-size: clamp(12px, 1.5vw, 14px);
  color: var(--plaza-text-muted);
  line-height: 1.6;
}
.step-arrow {
  position: absolute;
  top: 30%;
  right: -6%;
  color: var(--plaza-text-muted);
  font-size: clamp(14px, 2vw, 20px);
}

/* CTA */
.cta {
  padding: clamp(50px, 8vw, 100px) 5vw;
  background: linear-gradient(135deg, var(--plaza-decor-wash-soft) 0%, transparent 100%);
}
.cta-content {
  max-width: 600px;
  margin: 0 auto;
  text-align: center;
}
.cta-title {
  font-size: clamp(1.5rem, 4vw, 2.5rem);
  font-weight: 700;
  color: var(--plaza-text);
  margin-bottom: 0.8rem;
}
.cta-desc {
  font-size: clamp(0.9rem, 1.8vw, 1.2rem);
  color: var(--plaza-text-muted);
  margin-bottom: 2rem;
}
.cta-actions {
  margin-bottom: 1.5rem;
}
.cta-btn {
  background: var(--plaza-accent) !important;
  border: none !important;
  color: #fff !important;
  font-weight: 600;
  padding: clamp(12px, 2vw, 16px) clamp(24px, 4vw, 36px) !important;
  border-radius: 0.8rem !important;
  font-size: clamp(13px, 1.8vw, 16px);
}
.cta-btn:hover {
  background: var(--plaza-accent-hover) !important;
  transform: translateY(-2px);
  box-shadow: 0 8px 24px var(--plaza-cta-shadow);
}
.cta-features {
  display: flex;
  justify-content: center;
  gap: clamp(12px, 3vw, 24px);
  flex-wrap: wrap;
}
.cta-feature-item {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: clamp(11px, 1.4vw, 14px);
  color: var(--plaza-text-muted);
}
.check-icon {
  color: var(--plaza-accent);
}

/* Footer */
.landing-footer {
  background: var(--plaza-bg-card);
  border-top: 1px solid var(--plaza-border);
  padding: clamp(30px, 5vw, 60px) 5vw clamp(12px, 2vw, 24px);
}
.footer-content {
  max-width: 1200px;
  margin: 0 auto;
  display: flex;
  justify-content: space-between;
  margin-bottom: clamp(20px, 4vw, 40px);
  gap: 2rem;
}
.footer-brand {
  max-width: 280px;
}
.footer-tagline {
  margin-top: 0.8rem;
  font-size: clamp(11px, 1.4vw, 14px);
  color: var(--plaza-text-muted);
}
.footer-links {
  display: flex;
  gap: clamp(30px, 5vw, 60px);
}
.footer-link-group {
  display: flex;
  flex-direction: column;
  gap: 0.8rem;
}
.footer-link-title {
  font-size: clamp(11px, 1.4vw, 14px);
  font-weight: 600;
  color: var(--plaza-text);
  margin-bottom: 0.3rem;
}
.footer-link {
  font-size: clamp(11px, 1.4vw, 14px);
  color: var(--plaza-text-muted);
  text-decoration: none;
  transition: color 0.2s ease;
}
.footer-link:hover {
  color: var(--plaza-accent);
}
.footer-bottom {
  max-width: 1200px;
  margin: 0 auto;
  padding-top: 1.5rem;
  border-top: 1px solid var(--plaza-border);
  text-align: center;
  font-size: clamp(11px, 1.3vw, 13px);
  color: var(--plaza-text-muted);
}

/* Animations */
@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* Responsive */
@media (max-width: 1024px) {
  .features-grid {
    grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  }
  .steps {
    flex-wrap: wrap;
    justify-content: center;
  }
  .step-item {
    flex: 0 0 calc(50% - 1rem);
  }
  .step-arrow {
    display: none;
  }
}

@media (max-width: 768px) {
  .landing-nav-inner {
    padding: 1rem 4vw;
  }
  .hero-title {
    font-size: clamp(1.5rem, 6vw, 2.5rem);
  }
  .hero-subtitle {
    font-size: clamp(0.85rem, 2vw, 1rem);
  }
  .section-title {
    font-size: clamp(1.3rem, 4vw, 2rem);
  }
  .features-grid {
    grid-template-columns: 1fr;
  }
  .steps {
    flex-direction: column;
    gap: 2rem;
  }
  .step-item {
    flex: 1;
    width: 100%;
  }
  .footer-content {
    flex-direction: column;
    gap: 2rem;
  }
  .footer-links {
    flex-wrap: wrap;
    gap: 2rem;
  }
}

@media (max-width: 480px) {
  .landing-nav-actions .el-button {
    padding: 8px 16px !important;
    font-size: 12px;
  }
  .hero {
    padding: 70px 4vw 50px;
  }
  .hero-badge {
    font-size: 11px;
    padding: 6px 12px;
  }
  .cta-btn {
    width: 100%;
  }
}
</style>
