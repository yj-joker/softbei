<template>
  <div class="page-container">
    <nav class="navbar">
      <div class="nav-logo">
        <div class="nav-logo-icon">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
            <path d="M22.7 19l-9.1-9.1c.9-2.3.4-5-1.5-6.9-2-2-5-2.4-7.4-1.3L9 6 6 9 1.6 4.7C.4 7.1.9 10.1 2.9 12.1c1.9 1.9 4.6 2.4 6.9 1.5l9.1 9.1c.4.4 1 .4 1.4 0l2.3-2.3c.5-.4.5-1.1.1-1.4z"/>
          </svg>
        </div>
        设备检修系统
      </div>
      <ul class="nav-menu">
        <li><a href="#" @click.prevent="goTo('/home')">首页</a></li>
        <li><a href="#" @click.prevent="goTo('/knowledge')">知识检索</a></li>
        <li><a href="#" @click.prevent="goTo('/operation')">作业指引</a></li>
        <li><a href="#" @click.prevent="goTo('/cases')">检修案例</a></li>
        <li><a href="#" class="active" @click.prevent="goTo('/profile')">个人中心</a></li>
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
        <h2>个人中心</h2>
        <p>管理您的账户设置和偏好</p>
      </div>

      <div class="profile-container">
        <div class="profile-card">
          <div class="profile-avatar">张</div>
          <h3>张三</h3>
          <p>技术支持工程师</p>
        </div>

        <div class="settings-card">
          <h4>账户设置</h4>
          <div class="setting-item">
            <span>修改密码</span>
            <span class="arrow">›</span>
          </div>
          <div class="setting-item">
            <span>通知设置</span>
            <span class="arrow">›</span>
          </div>
          <div class="setting-item">
            <span>偏好设置</span>
            <span class="arrow">›</span>
          </div>
        </div>

        <button class="logout-btn" @click="handleLogout">退出登录</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { useRouter } from 'vue-router'
import { useSpeech } from '@/composables/useSpeech'

const router = useRouter()
const { stop: stopSpeech } = useSpeech()

const goTo = (path) => {
  router.push(path)
}

const handleLogout = () => {
  stopSpeech()
  router.push('/login')
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
  filter: drop-shadow(0 0 4px rgba(255, 215, 0, 0.8));
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

.profile-container {
  width: 90%;
  max-width: 600px;
  margin: 0 auto;
}

.profile-card {
  background: white;
  padding: clamp(1.5rem, 4vw, 40px);
  border-radius: 1rem;
  text-align: center;
  margin-bottom: 2rem;
}

.profile-avatar {
  width: clamp(60px, 12vw, 80px);
  height: clamp(60px, 12vw, 80px);
  background: linear-gradient(135deg, var(--primary-blue), var(--light-blue));
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  font-size: clamp(24px, 4vw, 32px);
  font-weight: 600;
  margin: 0 auto 1.2rem;
}

.profile-card h3 {
  font-size: clamp(18px, 2.5vw, 24px);
  color: var(--text-dark);
  margin-bottom: 0.5rem;
}

.profile-card p {
  font-size: clamp(12px, 1.5vw, 14px);
  color: var(--text-gray);
}

.settings-card {
  background: white;
  border-radius: 1rem;
  padding: clamp(1rem, 2vw, 20px);
  margin-bottom: 2rem;
}

.settings-card h4 {
  font-size: clamp(14px, 1.8vw, 16px);
  color: var(--text-dark);
  margin-bottom: 1rem;
  padding-bottom: 1rem;
  border-bottom: 1px solid var(--border-color);
}

.setting-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.8rem 0;
  border-bottom: 1px solid var(--border-color);
  cursor: pointer;
  transition: background 0.3s;
}

.setting-item:last-child {
  border-bottom: none;
}

.setting-item:hover {
  background: var(--hover-blue);
  margin: 0 -2vw;
  padding: 0.8rem 2vw;
}

.setting-item span {
  color: var(--text-dark);
  font-size: clamp(13px, 1.5vw, 15px);
}

.setting-item .arrow {
  color: var(--text-gray);
  font-size: clamp(16px, 2vw, 20px);
}

.logout-btn {
  width: 100%;
  padding: 1rem;
  background: white;
  color: #e74c3c;
  border: 2px solid #e74c3c;
  border-radius: 0.6rem;
  font-size: clamp(13px, 1.5vw, 16px);
  font-weight: 600;
  cursor: pointer;
  transition: all 0.3s;
}

.logout-btn:hover {
  background: #e74c3c;
  color: white;
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

  .setting-item:hover {
    margin: 0 -3vw;
    padding: 0.8rem 3vw;
  }
}
</style>
