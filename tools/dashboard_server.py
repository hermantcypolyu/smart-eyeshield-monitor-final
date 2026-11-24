#!/usr/bin/env python3
"""
Real-time Dashboard WebSocket Server for Eye-Shield Monitor
============================================================
Streams live data from the AI detection system and ESP32 hardware
to the web dashboard.

Run:
    python tools/dashboard_server.py
"""

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
import websockets
from collections import deque

# Simulated data for demonstration - replace with real data sources
class DashboardDataSource:
    def __init__(self):
        self.fps_history = deque(maxlen=60)
        self.violation_count = 0
        self.safe_count = 0
        self.total_frames = 0
        self.start_time = time.time()
        self.current_state = "SAFE"
        self.last_incident_time = None
        self.esp32_connected = True
        self.camera_connected = True
        
    def get_current_data(self):
        """Generate current dashboard data"""
        now = time.time()
        uptime = int(now - self.start_time)
        
        # Simulate FPS
        fps = 18.0 + (hash(str(now)) % 30) / 10.0
        self.fps_history.append(fps)
        
        # Calculate metrics
        avg_fps = sum(self.fps_history) / len(self.fps_history) if self.fps_history else 0
        
        return {
            "timestamp": datetime.now().isoformat(),
            "state": {
                "current": self.current_state,
                "color": self._get_state_color(),
                "icon": self._get_state_icon(),
                "description": self._get_state_description(),
                "progress": self._get_progress()
            },
            "camera": {
                "fps": round(fps, 1),
                "avg_fps": round(avg_fps, 1),
                "resolution": "640x480",
                "device": "mps",
                "connected": self.camera_connected
            },
            "health": {
                "serial": self.esp32_connected,
                "camera": self.camera_connected,
                "model": True,
                "inference": avg_fps > 10
            },
            "hardware": {
                "green_led": self.current_state == "SAFE",
                "red_led": self.current_state in ["ALARM_LATCHED", "ACKNOWLEDGED"],
                "buzzer": self.current_state == "ALARM_LATCHED"
            },
            "metrics": {
                "total_frames": self.total_frames,
                "violations": self.violation_count,
                "safe_detections": self.safe_count,
                "uptime_seconds": uptime,
                "model": "best_v2.pt"
            },
            "incident": {
                "id": 1 if self.current_state != "SAFE" else None,
                "acknowledged": self.current_state == "ACKNOWLEDGED",
                "safe_recovered": False,
                "last_violation": self.last_incident_time
            },
            "thresholds": {
                "candidate": 0.40,
                "goggles": 0.60,
                "no_goggles": 0.55,
                "iou": 0.30
            }
        }
    
    def _get_state_color(self):
        colors = {
            "SAFE": "green",
            "ALARM_LATCHED": "red",
            "ACKNOWLEDGED": "amber",
            "UNKNOWN": "muted"
        }
        return colors.get(self.current_state, "muted")
    
    def _get_state_icon(self):
        icons = {
            "SAFE": "✓",
            "ALARM_LATCHED": "⚠",
            "ACKNOWLEDGED": "◉",
            "UNKNOWN": "?"
        }
        return icons.get(self.current_state, "?")
    
    def _get_state_description(self):
        descriptions = {
            "SAFE": "Goggles detected · System nominal",
            "ALARM_LATCHED": "No goggles · Alarm active · Press A to acknowledge",
            "ACKNOWLEDGED": "Violation acknowledged · Buzzer silenced · Press R to reset",
            "UNKNOWN": "Detection uncertain"
        }
        return descriptions.get(self.current_state, "")
    
    def _get_progress(self):
        """Get progress for pending state transitions"""
        if self.current_state == "SAFE":
            return 0
        elif self.current_state == "ALARM_LATCHED":
            return 100
        elif self.current_state == "ACKNOWLEDGED":
            return 50
        return 0
    
    def simulate_cycle(self):
        """Simulate state transitions for demo"""
        self.total_frames += 1
        
        # Every 100 frames, cycle through states
        cycle_pos = (self.total_frames // 100) % 4
        
        if cycle_pos == 0:
            self.current_state = "SAFE"
            self.safe_count += 1
        elif cycle_pos == 1:
            self.current_state = "ALARM_LATCHED"
            if self.total_frames % 100 == 1:
                self.violation_count += 1
                self.last_incident_time = datetime.now().isoformat()
        elif cycle_pos == 2:
            self.current_state = "ACKNOWLEDGED"
        else:
            self.current_state = "SAFE"


# Global data source
data_source = DashboardDataSource()

# Connected clients
connected_clients = set()


async def send_updates():
    """Continuously send updates to all connected clients"""
    while True:
        if connected_clients:
            # Simulate state cycle
            data_source.simulate_cycle()
            
            # Get current data
            data = data_source.get_current_data()
            message = json.dumps(data)
            
            # Send to all clients
            disconnected = set()
            for websocket in connected_clients:
                try:
                    await websocket.send(message)
                except websockets.exceptions.ConnectionClosed:
                    disconnected.add(websocket)
            
            # Remove disconnected clients
            connected_clients.difference_update(disconnected)
        
        # Update every 50ms (20 FPS)
        await asyncio.sleep(0.05)


async def handler(websocket):
    """Handle WebSocket connections"""
    print(f"Client connected from {websocket.remote_address}")
    connected_clients.add(websocket)
    
    try:
        # Send initial data
        data = data_source.get_current_data()
        await websocket.send(json.dumps(data))
        
        # Keep connection alive and handle incoming messages
        async for message in websocket:
            # Handle commands from dashboard (optional)
            try:
                command = json.loads(message)
                print(f"Received command: {command}")
                # Handle commands here (e.g., acknowledge, reset)
            except json.JSONDecodeError:
                pass
    
    except websockets.exceptions.ConnectionClosed:
        pass
    
    finally:
        connected_clients.discard(websocket)
        print(f"Client disconnected from {websocket.remote_address}")


async def main():
    """Start the WebSocket server"""
    print("=" * 70)
    print("Eye-Shield Dashboard Server")
    print("=" * 70)
    print(f"Starting WebSocket server on ws://localhost:8765")
    print(f"Open the dashboard HTML file in your browser")
    print(f"Press Ctrl+C to stop")
    print("=" * 70)
    
    # Start update task
    update_task = asyncio.create_task(send_updates())
    
    # Start WebSocket server
    async with websockets.serve(handler, "localhost", 8765):
        await asyncio.Future()  # Run forever


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutting down...")
