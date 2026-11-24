# 🎯 Quick Start - Dashboard

## 一键启动仪表板

### 方法 1：使用启动脚本（推荐）

```bash
cd /Users/hermanyeung/Downloads/AIOT-MiniProject
./dashboard/start.sh
```

### 方法 2：手动启动

```bash
cd /Users/hermanyeung/Downloads/AIOT-MiniProject

# 1. 安装依赖（只需一次）
.venv/bin/pip install websockets

# 2. 启动服务器
.venv/bin/python tools/dashboard_server.py
```

---

## 打开仪表板

### 在新终端或浏览器中：

```bash
# 方法 1：命令行打开
open dashboard/index.html

# 方法 2：手动打开浏览器，输入地址
file:///Users/hermanyeung/Downloads/AIOT-MiniProject/dashboard/index.html
```

---

## ✅ 验证仪表板正常工作

看到以下内容说明成功：

1. **终端显示**：
   ```
   ======================================================================
   Eye-Shield Dashboard Server
   ======================================================================
   Starting WebSocket server on ws://localhost:8765
   Client connected from ('127.0.0.1', xxxxx)
   ```

2. **浏览器右上角显示**：`● Connected`（绿色）

3. **仪表板实时更新**：
   - FPS 数字跳动
   - 系统状态循环变化（SAFE → ALARM_LATCHED → ACKNOWLEDGED）
   - LED 状态实时变化

---

## 🎬 演示数据

当前仪表板使用模拟数据演示功能：

- 每 5 秒循环一次完整流程
- 自动模拟状态转换
- 实时更新所有指标

---

## 🔌 连接到真实系统

要连接到实际的 AI 检测系统，需要修改 `tools/live_eye_shield_demo.py`。

详细说明见：`dashboard/README.md`

---

## 📸 截图说明

仪表板包含：

- **左侧**：摄像头视图 + 检测可视化 + FPS
- **右侧**：
  - 系统状态（SAFE / ALARM / ACKNOWLEDGED）
  - 系统健康（串口、摄像头、AI 模型）
  - 硬件输出（绿灯、红灯、蜂鸣器）

---

## 🛑 停止服务器

在运行 `dashboard_server.py` 的终端按 `Ctrl+C`
