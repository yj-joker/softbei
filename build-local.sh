#!/bin/bash
# ============================================================
# build-local.sh — 本地构建 + 打包部署包
# 在项目根目录（softbei/）执行：bash build-local.sh
# 产物：softbei/deploy/  上传这个目录到服务器即可
# ============================================================
set -e

# Use the JDK configured by the current machine. This keeps the build entry
# portable across Windows Git Bash, x86 Linux and LoongArch Linux.
if [ -n "${JAVA_HOME:-}" ]; then
  export PATH="$JAVA_HOME/bin:$PATH"
fi

for command_name in java mvn node npm; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Missing required command: $command_name" >&2
    exit 1
  fi
done

ROOT="$(cd "$(dirname "$0")" && pwd)"
DEPLOY="$ROOT/deploy"

echo "▶ 清理旧部署包..."
rm -rf "$DEPLOY"
mkdir -p "$DEPLOY/frontend" "$DEPLOY/FixAgent"

# ── ① weixiu（Java）──────────────────────────────────────────
echo "▶ 构建 weixiu（Maven + JDK21，可能需要几分钟）..."
cd "$ROOT/weixiu"
mvn clean package -DskipTests -q
cp target/*.jar "$DEPLOY/weixiu.jar"
cp "$ROOT/.env.example" "$DEPLOY/.env.example"
echo "  ✓ weixiu.jar 打包完成"

# ── ② 前端（Vue3 + Vite）────────────────────────────────────
echo "▶ 构建前端..."
cd "$ROOT/fix-"
npm ci --silent
npm run build --silent
cp -r dist/* "$DEPLOY/frontend/"
echo "  ✓ 前端构建完成"

# ── ③ FixAgent（Python，直接拷源码）─────────────────────────
echo "▶ 打包 FixAgent..."
cp -r "$ROOT/FixAgent/." "$DEPLOY/FixAgent/"
# .venv 太大不上传，服务器会自动重建
rm -rf "$DEPLOY/FixAgent/.venv"
echo "  ✓ FixAgent 已打包"

# ── ④ Nginx 配置（把容器名 weixiu 换成 localhost）──────────
echo "▶ 生成 nginx.conf（weixiu:8080 → localhost:8080）..."
sed 's|http://weixiu:8080|http://localhost:8080|g' \
  "$ROOT/fix-/nginx.conf" > "$DEPLOY/nginx.conf"
echo "  ✓ nginx.conf 已生成"

# ── ⑤ 生成服务器启动/停止脚本 ───────────────────────────────
echo "▶ 生成 start.sh / stop.sh..."

cat > "$DEPLOY/start.sh" << 'EOF'
#!/bin/bash
# ============================================================
# start.sh — 服务器一键启动
# 用法：bash /opt/softbei/start.sh
# ============================================================
set -e

DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$DEPLOY_DIR/logs"
mkdir -p "$LOG_DIR"

echo "=== [1/4] 停止旧进程 ==="
pkill -f 'java.*weixiu' 2>/dev/null && echo "  已停止 weixiu" || echo "  weixiu 未运行，跳过"
pkill -f 'uvicorn api.main' 2>/dev/null && echo "  已停止 FixAgent" || echo "  FixAgent 未运行，跳过"
sleep 2

echo "=== [2/4] 部署前端 + 更新 Nginx ==="
sudo cp -r "$DEPLOY_DIR/frontend/." /usr/share/nginx/html/
sudo cp "$DEPLOY_DIR/nginx.conf" /etc/nginx/conf.d/default.conf
sudo nginx -t && sudo systemctl reload nginx
echo "  ✓ Nginx 已更新"

echo "=== [3/4] 启动 weixiu（Java 后端） ==="
nohup java -jar "$DEPLOY_DIR/weixiu.jar" \
  > "$LOG_DIR/weixiu.log" 2>&1 &
WEIXIU_PID=$!
echo "  ✓ weixiu 已启动，PID=$WEIXIU_PID"

echo "=== [4/4] 启动 FixAgent（Python 后端） ==="
cd "$DEPLOY_DIR/FixAgent"
if [ ! -d ".venv" ]; then
  echo "  首次运行：创建 Python 虚拟环境..."
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -r requirements.txt
nohup uvicorn api.main:app \
  --host 0.0.0.0 --port 8000 \
  > "$LOG_DIR/fixagent.log" 2>&1 &
AGENT_PID=$!
echo "  ✓ FixAgent 已启动，PID=$AGENT_PID"

echo ""
echo "=== 等待服务就绪（5秒）... ==="
sleep 5

echo ""
echo "=== 端口监听状态 ==="
ss -tlnp | grep -E '8080|8000|:80 ' || echo "  （端口可能还在启动中）"

echo ""
echo "✅ 全部启动完成！"
echo "   日志目录：$LOG_DIR/"
echo "   weixiu  → tail -f $LOG_DIR/weixiu.log"
echo "   FixAgent → tail -f $LOG_DIR/fixagent.log"
EOF

cat > "$DEPLOY/stop.sh" << 'EOF'
#!/bin/bash
# stop.sh — 停止所有服务
pkill -f 'java.*weixiu' 2>/dev/null && echo "weixiu 已停止" || echo "weixiu 未运行"
pkill -f 'uvicorn api.main' 2>/dev/null && echo "FixAgent 已停止" || echo "FixAgent 未运行"
echo "完成。（Nginx 仍在运行，如需停止：sudo systemctl stop nginx）"
EOF

chmod +x "$DEPLOY/start.sh" "$DEPLOY/stop.sh"
echo "  ✓ 脚本已生成"

# ── 完成 ─────────────────────────────────────────────────────
echo ""
echo "============================================"
echo " ✅ 构建完成！deploy/ 目录内容："
ls -lh "$DEPLOY"
echo "============================================"
echo ""
echo " 下一步：上传并启动"
echo "   1. 上传："
echo "      scp -r deploy/ user@服务器IP:/opt/softbei/"
echo ""
echo "   2. 服务器上启动："
echo "      bash /opt/softbei/start.sh"
echo "============================================"
