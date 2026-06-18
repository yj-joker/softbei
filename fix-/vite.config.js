import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'

export default defineConfig({
  plugins: [vue()],
  // sockjs-client 依赖全局 global 变量，浏览器环境用 globalThis 垫一下
  define: { global: 'globalThis' },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src')
    }
  },
  server: {
    port: 3000,
    open: false,
    proxy: {
      '/api/asr': {
        target: 'http://localhost:8080/weixiu/ai',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/asr/, ''),
      },
      '/weixiu/ai': {
        target: 'http://localhost:8080',
        changeOrigin: true,
        ws: true, // 允许实时语音识别的 WebSocket(/weixiu/ai/asr-stream) 经代理升级
      },
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
        ws: true, // 允许 SockJS 的 websocket 传输经代理升级（/api/ws）
        rewrite: (path) => path.replace(/^\/api/, ''),
      }
    }
  }
})