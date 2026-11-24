# Smart Eye-Shield Safety Dashboard

Real-time operations dashboard for the Eye-Shield AI safety monitoring system.

## 🚀 Quick Start

### 1. Install WebSocket dependency

```bash
pip install websockets
```

### 2. Start the dashboard server

```bash
# In terminal 1
cd /Users/hermanyeung/Downloads/AIOT-MiniProject
.venv/bin/python tools/dashboard_server.py
```

You should see:
```
======================================================================
Eye-Shield Dashboard Server
======================================================================
Starting WebSocket server on ws://localhost:8765
Open the dashboard HTML file in your browser
Press Ctrl+C to stop
======================================================================
```

### 3. Open the dashboard in your browser

```bash
# In terminal 2 (or just open in browser)
open dashboard/index.html
```

Or manually open: `file:///Users/hermanyeung/Downloads/AIOT-MiniProject/dashboard/index.html`

The dashboard will automatically connect to the WebSocket server and display real-time data.

---

## 📊 Dashboard Features

### Real-time Monitoring

- **System State**: Current safety state (SAFE, ALARM_LATCHED, ACKNOWLEDGED)
- **Camera Feed**: Live FPS, frame count, and detection status
- **Hardware Status**: ESP32 LED and buzzer states
- **System Health**: Serial connection, camera, AI model, and inference health

### Visual Indicators

- **Green**: System SAFE, goggles detected
- **Red**: Violation detected, no goggles
- **Amber**: Violation acknowledged, awaiting reset
- **Scanning Animation**: Real-time AI detection indicator

---

## 🔧 Integration with Live System

To connect the dashboard to your **actual** live Eye-Shield system:

### Option 1: Run with simulated data (current setup)

```bash
.venv/bin/python tools/dashboard_server.py
```

The server generates simulated state transitions for demonstration.

### Option 2: Integrate with real-time detection

Modify `tools/live_eye_shield_demo.py` to publish data to the dashboard server:

```python
# Add at the top of live_eye_shield_demo.py
import asyncio
import websockets
import json

# Create a global dashboard connection
dashboard_ws = None

async def publish_to_dashboard(data):
    global dashboard_ws
    if dashboard_ws:
        try:
            await dashboard_ws.send(json.dumps(data))
        except:
            dashboard_ws = None

# In the main loop, after each frame:
if dashboard_ws:
    asyncio.create_task(publish_to_dashboard({
        "state": {"current": status.state.name, ...},
        "camera": {"fps": current_fps, ...},
        # ... more data
    }))
```

---

## 🎯 What the Dashboard Shows

### Left Panel: Camera View
- **Detection visualization** with scanning animation
- **Real-time FPS** counter
- **Model information** (best_v2.pt)
- **Threshold values** (goggles=0.60, no_goggles=0.55)

### Right Panel: System Status

#### 1. SYSTEM STATE
- Current state name (SAFE / ALARM_LATCHED / ACKNOWLEDGED)
- Color-coded state indicator
- Progress bar for state transitions
- Total frames and violation counters

#### 2. SYSTEM HEALTH
- ✅ ESP32 Serial: Serial connection status
- ✅ Camera Feed: Camera operational
- ✅ AI Model: Model loaded
- ✅ Inference: Detection running at >10 FPS

#### 3. HARDWARE OUTPUTS
- 🟢 Green LED: Safe state indicator
- 🔴 Red LED: Violation/alarm indicator
- 🔊 Buzzer: Active alarm indicator

---

## 📡 WebSocket Protocol

The dashboard server sends JSON messages at 20 FPS:

```json
{
  "timestamp": "2026-11-22T14:30:00.000",
  "state": {
    "current": "SAFE",
    "color": "green",
    "icon": "✓",
    "description": "Goggles detected · System nominal",
    "progress": 0
  },
  "camera": {
    "fps": 18.5,
    "avg_fps": 18.2,
    "resolution": "640x480",
    "device": "mps",
    "connected": true
  },
  "health": {
    "serial": true,
    "camera": true,
    "model": true,
    "inference": true
  },
  "hardware": {
    "green_led": true,
    "red_led": false,
    "buzzer": false
  },
  "metrics": {
    "total_frames": 12345,
    "violations": 5,
    "safe_detections": 12340,
    "uptime_seconds": 3600,
    "model": "best_v2.pt"
  },
  "thresholds": {
    "candidate": 0.40,
    "goggles": 0.60,
    "no_goggles": 0.55,
    "iou": 0.30
  }
}
```

---

## 🛠️ Troubleshooting

### Dashboard shows "Disconnected"

1. **Check if the server is running**:
   ```bash
   ps aux | grep dashboard_server
   ```

2. **Restart the server**:
   ```bash
   .venv/bin/python tools/dashboard_server.py
   ```

3. **Check the port is not in use**:
   ```bash
   lsof -i :8765
   ```

### Browser can't connect to WebSocket

1. **Open browser console** (F12 → Console)
2. Check for errors like "Connection refused"
3. Ensure the server is running **before** opening the HTML file
4. Try refreshing the page

### No data updates

1. Check the terminal running `dashboard_server.py` for errors
2. Look for "Client connected" messages
3. Restart both the server and browser

---

## 📝 Files

- `dashboard/index.html` - Dashboard web interface
- `tools/dashboard_server.py` - WebSocket server for real-time data
- `dashboard/README.md` - This file

---

## 🎓 Project Info

**Course**: EIE Mini Project  
**Institution**: The Hong Kong Polytechnic University  
**Project**: Smart Eye-Shield Safety Monitoring System  
**AI Model**: YOLOv8n (best_v2.pt)  
**Hardware**: ESP32-S3, LEDs, Buzzer  
