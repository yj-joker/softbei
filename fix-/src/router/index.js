import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    name: 'Home',
    component: () => import('../views/homeviews/Home.vue')
  },
  {
    path: '/login',
    name: 'Login',
    component: () => import('../views/homeviews/Login.vue')
  },
  {
    path: '/knowledge',
    name: 'Knowledge',
    component: () => import('../views/Knowledge.vue')
  },
  {
    path: '/operation',
    name: 'Operation',
    component: () => import('../views/Operation.vue')
  },
  {
    path: '/cases',
    name: 'Cases',
    component: () => import('../views/Cases.vue')
  },
  {
    path: '/profile',
    name: 'Profile',
    component: () => import('../views/Profile.vue')
  },
  {
    path: '/forgot-password',
    name: 'ForgotPassword',
    component: () => import('../views/homeviews/ForgotPassword.vue')
  },
  {
    path: '/admin',
    name: 'Admin',
    component: () => import('../views/adminViews/AdminLayout.vue'),
    redirect: '/admin/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'AdminDashboard',
        component: () => import('../views/adminViews/AdminDashboard.vue')
      },
      {
        path: 'users',
        name: 'AdminUsers',
        redirect: { path: '/admin/system', query: { tab: 'users' } }
      },
      {
        path: 'knowledge',
        name: 'AdminKnowledge',
        redirect: { path: '/admin/knowledge-center', query: { tab: 'knowledge' } }
      },
      {
        path: 'knowledge-center',
        name: 'AdminKnowledgeCenter',
        component: () => import('../views/adminViews/AdminKnowledgeCenter.vue')
      },
      {
        path: 'graph',
        name: 'AdminKnowledgeGraph',
        redirect: { path: '/admin/knowledge-center', query: { tab: 'graph' } }
      },
      {
        path: 'ai-chat',
        name: 'AdminAIChat',
        component: () => import('../views/adminViews/AdminAIChat.vue')
      },
      {
        path: 'domain-rules',
        name: 'AdminDomainRules',
        redirect: { path: '/admin/knowledge-center', query: { tab: 'domain-rules' } }
      },
      {
        path: 'settings',
        name: 'AdminSettings',
        redirect: { path: '/admin/system', query: { tab: 'users' } }
      },
      {
        path: 'profile',
        name: 'AdminProfile',
        component: () => import('../views/adminViews/AdminProfile.vue')
      },
      {
        path: 'notify',
        name: 'AdminNotify',
        redirect: { path: '/admin/system', query: { tab: 'notify' } }
      },
      {
        path: 'tasks',
        name: 'AdminTasks',
        component: () => import('../views/adminViews/AdminTasks.vue')
      },
      {
        path: 'procedures',
        name: 'AdminProcedures',
        redirect: { path: '/admin/knowledge-center', query: { tab: 'procedures' } }
      },
      {
        path: 'system',
        name: 'AdminSystemCenter',
        component: () => import('../views/adminViews/AdminSystemCenter.vue')
      }
    ]
  },
  {
    path: '/user',
    name: 'User',
    component: () => import('../views/userViews/UserLayout.vue'),
    redirect: '/user/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'UserDashboard',
        component: () => import('../views/userViews/UserDashboard.vue')
      },
      {
        path: 'search',
        name: 'UserSearch',
        component: () => import('../views/userViews/UserSearch.vue')
      },
      {
        path: 'graph',
        name: 'UserKnowledgeGraph',
        component: () => import('../views/userViews/UserKnowledgeGraph.vue')
      },
      {
        path: 'ai-chat',
        name: 'UserAIChat',
        component: () => import('../views/userViews/UserAIChat.vue')
      },
      {
        path: 'quiz',
        name: 'UserQuiz',
        component: () => import('../views/userViews/UserQuiz.vue')
      },
      {
        path: 'case-upload',
        name: 'UserCaseUpload',
        component: () => import('../views/userViews/UserCaseUpload.vue')
      },
      {
        path: 'profile',
        name: 'UserProfile',
        component: () => import('../views/userViews/UserProfile.vue')
      },
      {
        path: 'search-result',
        name: 'UserSearchResult',
        component: () => import('../views/userViews/UserSearchResult.vue')
      },
      {
        path: 'tasks',
        name: 'UserTasks',
        component: () => import('../views/userViews/UserTasks.vue')
      },
      {
        path: 'tasks/:id',
        name: 'UserTaskDetail',
        component: () => import('../views/userViews/UserTaskDetail.vue')
      }
    ]
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'NotFound',
    component: () => import('../views/NotFound.vue')
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

// 全局登录守卫：/admin 与 /user 下的页面需登录，未登录直接跳登录页，
// 从根上避免「未登录就挂载布局 → 触发 WebSocket 握手鉴权失败」。
router.beforeEach((to, from, next) => {
  const rawUserInfo = localStorage.getItem('userInfo')
  const isAuthed = !!rawUserInfo
  const needsAuth = to.path.startsWith('/admin') || to.path.startsWith('/user')
  if (needsAuth && !isAuthed) {
    next({ path: '/login', query: { redirect: to.fullPath } })
    return
  }

  if (needsAuth) {
    let userType = null
    try {
      userType = Number(JSON.parse(rawUserInfo || '{}').type)
    } catch {
      localStorage.removeItem('userInfo')
      next({ path: '/login', query: { redirect: to.fullPath } })
      return
    }

    if (to.path.startsWith('/admin') && userType !== 1) {
      next('/user/dashboard')
      return
    }

    if (to.path.startsWith('/user') && userType === 1) {
      next('/admin/dashboard')
      return
    }
  }

  next()
})

export default router
