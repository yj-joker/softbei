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
        <li><a href="#" class="active" @click.prevent="goTo('/knowledge')">知识检索</a></li>
        <li><a href="#" @click.prevent="goTo('/operation')">作业指引</a></li>
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
        <h2>多模态知识检索</h2>
        <p>支持文本、图像、设备型号等多类型检索</p>
      </div>

      <div class="search-container">
        <div class="search-box">
          <input type="text" class="search-input" placeholder="请输入故障描述、设备型号或关键词..." v-model="searchQuery">
          <button class="search-btn">搜索</button>
        </div>
      </div>

      <div class="filter-section">
        <div class="filter-item">
          <label>设备类型</label>
          <select v-model="equipmentType">
            <option value="">全部</option>
            <option value="engine">发动机</option>
            <option value="motor">电机</option>
            <option value="electrical">电气设备</option>
          </select>
        </div>
        <div class="filter-item">
          <label>检索模式</label>
          <select v-model="searchMode">
            <option value="text">文本检索</option>
            <option value="image">图像检索</option>
            <option value="multi">多模态检索</option>
          </select>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const searchQuery = ref('')
const equipmentType = ref('')
const searchMode = ref('text')

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
  margin-bottom: 4vw;
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

.search-container {
  width: 90%;
  max-width: 800px;
  margin: 0 auto 4vw;
}

.search-box {
  display: flex;
  background: white;
  border-radius: 1rem;
  padding: 0.5rem;
  border: 2px solid var(--border-color);
  transition: all 0.3s;
}

.search-box:focus-within {
  border-color: var(--primary-blue);
  box-shadow: 0 0 0 3px rgba(26, 95, 180, 0.1);
}

.search-input {
  flex: 1;
  padding: 1rem 1.2rem;
  border: none;
  background: transparent;
  font-size: clamp(12px, 1.8vw, 16px);
  outline: none;
}

.search-btn {
  padding: 1rem 2rem;
  background: linear-gradient(135deg, var(--primary-blue), var(--light-blue));
  color: white;
  border: none;
  border-radius: 0.8rem;
  font-size: clamp(12px, 1.5vw, 16px);
  font-weight: 600;
  cursor: pointer;
  transition: all 0.3s;
  white-space: nowrap;
}

.search-btn:hover {
  transform: translateY(-2px);
  box-shadow: 0 5px 15px rgba(26, 95, 180, 0.3);
}

.filter-section {
  display: flex;
  flex-wrap: wrap;
  gap: 1.5rem;
  width: 90%;
  max-width: 800px;
  margin: 0 auto;
  justify-content: center;
}

.filter-item {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  flex: 1;
  min-width: 150px;
}

.filter-item label {
  font-size: clamp(11px, 1.4vw, 14px);
  color: var(--text-gray);
  font-weight: 500;
}

.filter-item select {
  padding: 0.8rem 1rem;
  border: 2px solid var(--border-color);
  border-radius: 8px;
  font-size: clamp(11px, 1.4vw, 14px);
  outline: none;
  width: 100%;
}

.filter-item select:focus {
  border-color: var(--primary-blue);
}

/* 平板 */
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
    gap: 0;
  }

  .filter-section {
    flex-direction: column;
    align-items: stretch;
  }

  .filter-item {
    min-width: unset;
  }
}

/* 手机 */
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

  .search-box {
    flex-direction: column;
    padding: 0.8rem;
  }

  .search-btn {
    width: 100%;
    margin-top: 0.5rem;
  }
}
</style>
