<template>
  <div class="page-container">
    <nav class="navbar">
      <div class="nav-logo">
        <div class="nav-logo-icon">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
            <path d="M22.7 19l-9.1-9.1c.9-2.3.4-5-1.5-6.9-2-2-5-2.4-7.4-1.3L9 6 6 9 1.6 4.7C.4 7.1.9 10.1 2.9 12.1c1.9 1.9 4.6 2.4 6.9 1.5l9.1 9.1c.4.4 1 .4 1.4 0l2.3-2.3c.5-.4.5-1.1.1-1.4z"/>
          </svg>
        </div>
        智维
      </div>
      <ul class="nav-menu">
        <li><a href="#" @click.prevent="goTo('/home')">首页</a></li>
        <li><a href="#" @click.prevent="goTo('/knowledge')">知识检索</a></li>
        <li><a href="#" class="active" @click.prevent="goTo('/operation')">作业指引</a></li>
        <li><a href="#" @click.prevent="goTo('/cases')">检修案例</a></li>
        <li><a href="#" @click.prevent="goTo('/profile')">个人中心</a></li>
      </ul>
      <div class="nav-right">
        <div class="nav-user" @click="goTo('/profile')">
          <div class="user-avatar">张</div>
          <span>张三</span>
        </div>
      </div>
    </nav>

    <div class="page-content">
      <div class="section-header">
        <h2>标准化作业指引</h2>
        <p>提供全流程步骤化操作指导与合规校验</p>
      </div>

      <div class="operation-cards">
        <div class="operation-card">
          <div class="card-icon">维护</div>
          <h3>日常保养</h3>
          <p>设备日常维护保养标准流程</p>
        </div>
        <div class="operation-card">
          <div class="card-icon">检索</div>
          <h3>故障检修</h3>
          <p>故障诊断与修复操作指引</p>
        </div>
        <div class="operation-card">
          <div class="card-icon">合规</div>
          <h3>合规检查</h3>
          <p>检修作业合规性校验</p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { useRouter } from 'vue-router'

const router = useRouter()

const goTo = (path) => {
  router.push(path)
}
</script>

<style scoped>
.page-container {
  min-height: 100vh;
  background: var(--bg-light);
}

.navbar {
  background: white;
  padding: 0 5vw;
  display: flex;
  justify-content: space-between;
  align-items: center;
  height: 10vw;
  max-height: 70px;
  min-height: 50px;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.05);
  position: sticky;
  top: 0;
  z-index: 100;
}

.nav-logo {
  display: flex;
  align-items: center;
  font-size: clamp(14px, 2vw, 20px);
  font-weight: 700;
  color: var(--primary-blue);
}

.nav-logo-icon {
  position: relative;
  width: clamp(28px, 5vw, 36px);
  height: clamp(28px, 5vw, 36px);
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--plaza-accent);
  border-radius: 50%;
  margin-right: 0.8rem;
}

.nav-logo-icon::before {
  content: '';
  position: absolute;
  width: 100%;
  height: 100%;
  border: 3px solid rgba(255, 255, 255, 0.3);
  border-radius: 50%;
  animation: pulse-ring 2s ease-in-out infinite;
}

.nav-logo-icon svg {
  color: #fff;
  width: 40%;
  height: 40%;
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

.nav-menu {
  display: flex;
  list-style: none;
  gap: clamp(10px, 3vw, 40px);
}

.nav-menu a {
  text-decoration: none;
  color: var(--text-dark);
  font-weight: 500;
  font-size: clamp(12px, 1.5vw, 15px);
  padding: 0.5rem 0;
  position: relative;
  transition: color 0.3s;
}

.nav-menu a:hover,
.nav-menu a.active {
  color: var(--primary-blue);
}

.nav-menu a::after {
  content: '';
  position: absolute;
  bottom: 0;
  left: 0;
  width: 0;
  height: 2px;
  background: var(--primary-blue);
  transition: width 0.3s;
}

.nav-menu a:hover::after,
.nav-menu a.active::after {
  width: 100%;
}

.nav-right {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.nav-user {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  cursor: pointer;
  padding: 0.4rem 0.8rem;
  border-radius: 8px;
  transition: background 0.3s;
}

.nav-user:hover {
  background: var(--hover-blue);
}

.user-avatar {
  width: clamp(28px, 4vw, 36px);
  height: clamp(28px, 4vw, 36px);
  background: linear-gradient(135deg, var(--primary-blue), var(--light-blue));
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  font-weight: 600;
  font-size: clamp(10px, 1.2vw, 14px);
}

.page-content {
  padding: 5vw;
}

.section-header {
  text-align: center;
  margin-bottom: 5vw;
}

.section-header h2 {
  font-size: clamp(20px, 4vw, 32px);
  color: var(--text-dark);
  margin-bottom: 1rem;
}

.section-header p {
  color: var(--text-gray);
  font-size: clamp(12px, 1.8vw, 16px);
}

.operation-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
  gap: 2rem;
  max-width: 1000px;
  margin: 0 auto;
  padding: 0 2vw;
}

.operation-card {
  background: white;
  padding: clamp(1.5rem, 4vw, 40px);
  border-radius: 1rem;
  text-align: center;
  transition: all 0.3s;
  cursor: pointer;
  border: 2px solid transparent;
}

.operation-card:hover {
  border-color: var(--primary-blue);
  transform: translateY(-5px);
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.1);
}

.card-icon {
  width: clamp(60px, 12vw, 80px);
  height: clamp(60px, 12vw, 80px);
  background: linear-gradient(135deg, var(--primary-blue), var(--light-blue));
  border-radius: 1.5rem;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: clamp(24px, 5vw, 36px);
  margin: 0 auto 1.2rem;
}

.operation-card h3 {
  font-size: clamp(16px, 2vw, 20px);
  color: var(--text-dark);
  margin-bottom: 0.6rem;
}

.operation-card p {
  font-size: clamp(12px, 1.5vw, 14px);
  color: var(--text-gray);
}

@media (max-width: 768px) {
  .navbar {
    flex-wrap: wrap;
    height: auto;
    min-height: 60px;
    padding: 1rem 4vw;
    gap: 1rem;
  }

  .nav-menu {
    order: 3;
    width: 100%;
    justify-content: space-around;
  }

  .operation-cards {
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 1.5rem;
  }
}

@media (max-width: 480px) {
  .page-content {
    padding: 4vw 3vw;
  }

  .nav-logo-icon {
    display: none;
  }

  .nav-menu {
    gap: 0;
  }

  .nav-menu a {
    font-size: 11px;
    padding: 0.3rem 0.5rem;
  }

  .user-avatar {
    display: none;
  }

  .nav-right span {
    display: none;
  }

  .operation-cards {
    grid-template-columns: 1fr;
    padding: 0;
  }
}
</style>
