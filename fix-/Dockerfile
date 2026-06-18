# ===== 构建阶段：Node 打包 Vite 静态产物 =====
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

# ===== 运行阶段：Nginx 托管 + 反代后端 =====
FROM nginx:1.27-alpine
# SPA 静态产物
COPY --from=build /app/dist /usr/share/nginx/html
# 反代配置（/api、/weixiu/ai、WebSocket、SSE → weixiu:8080）
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
