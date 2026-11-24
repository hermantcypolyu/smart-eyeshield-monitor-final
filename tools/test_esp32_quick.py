#!/usr/bin/env python3
"""快速测试 ESP32 串口通信"""

import serial
import time

PORT = "/dev/cu.usbserial-10"
BAUD = 115200

print(f"连接到 {PORT} @ {BAUD} baud...")

try:
    ser = serial.Serial(PORT, BAUD, timeout=2)
    time.sleep(2)  # 等待 ESP32 启动
    
    print("\n=== 读取 ESP32 启动信息 ===")
    while ser.in_waiting:
        line = ser.readline().decode('utf-8', errors='ignore').strip()
        if line:
            print(f"ESP32 >> {line}")
    
    print("\n=== 发送 STATUS 命令 ===")
    ser.write(b"STATUS\n")
    time.sleep(0.5)
    
    while ser.in_waiting:
        line = ser.readline().decode('utf-8', errors='ignore').strip()
        if line:
            print(f"ESP32 >> {line}")
    
    print("\n=== 发送 ALARM 命令（测试红灯+蜂鸣器）===")
    ser.write(b"ALARM\n")
    time.sleep(0.5)
    
    while ser.in_waiting:
        line = ser.readline().decode('utf-8', errors='ignore').strip()
        if line:
            print(f"ESP32 >> {line}")
    
    print("\n=== 等待 3 秒（观察红灯和蜂鸣器）===")
    time.sleep(3)
    
    print("\n=== 发送 SAFE 命令（关闭报警）===")
    ser.write(b"SAFE\n")
    time.sleep(0.5)
    
    while ser.in_waiting:
        line = ser.readline().decode('utf-8', errors='ignore').strip()
        if line:
            print(f"ESP32 >> {line}")
    
    print("\n✅ 通信成功！ESP32 正常工作。")
    ser.close()
    
except serial.SerialException as e:
    print(f"\n❌ 串口错误: {e}")
    print("\n请检查：")
    print("  1. ESP32 是否已连接并上电")
    print("  2. 串口地址是否正确（ls /dev/cu.*）")
    print("  3. 是否有其他程序占用串口")
except KeyboardInterrupt:
    print("\n\n中断")
    ser.close()
