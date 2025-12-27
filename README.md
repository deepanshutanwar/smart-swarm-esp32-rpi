# Smart Swarm
## Distributed ESP32 Sensor Swarm with Raspberry Pi Visualization

Smart Swarm is a distributed IoT system built using multiple identical ESP32 devices and a Raspberry Pi.  
Each ESP32 node senses ambient light and participates in a dynamic **master election process** using UDP broadcast communication.  
The elected master forwards sensor data to a Raspberry Pi, which logs, visualizes, and serves the data through a **local web dashboard**.

---

## Schematic Diagram
<img width="1169" height="827" alt="Schematic_IoT-Assignment_2025-12-26" src="https://github.com/user-attachments/assets/79493f88-7930-4c9b-b7f7-85746981815a" />

---

## System Overview

### ESP32 Swarm
- All ESP32 devices run **identical firmware**
- Each node:
  - Reads a photocell sensor every ~100 ms
  - Broadcasts light values to the swarm
  - Participates in master election
  - Displays light intensity using an LED (PWM)
- The node with the **highest light value** becomes the **Master**
- The Master:
  - Turns ON its onboard LED
  - Broadcasts `MASTER:` messages to other ESPs and the Raspberry Pi

### Raspberry Pi
- Listens for UDP broadcast packets from ESP32 devices
- Logs all received data with timestamps
- Tracks master changes and master duration
- Hosts a **local web server** for visualization
- Supports system reset via hardware button

---

##  Communication Protocol

All communication uses **UDP broadcast** .

| Message Type | Sender | Description |
|--------------|--------|-------------|
| `LIGHT:<value>` | ESP32 (non-master) | Broadcast light sensor reading |
| `MASTER:<value>` | ESP32 (master) | Indicates current master device |
| `RESET` | Raspberry Pi | Resets all ESP32 nodes |

**Ports Used**
- ESP -> ESP: `4211`
- ESP -> Raspberry Pi: `4210`

---

## Features

### ESP32
- Dynamic master election based on sensor values
- Broadcast-based communication
- LED brightness mapped to light intensity
- Onboard LED indicates master status

### Raspberry Pi
- UDP packet listener
- Timestamped data logging
- Master tracking with timeout handling
- Local web server (Flask)
- Hardware button support:
  - Resets ESP32 swarm
  - Saves current log file
  - Starts a new session

---

## Data Logging

Each session creates a timestamped log file containing:
- Raw sensor readings
- Sender IP addresses
- Master transitions
- Total master duration per device

**Log format example:**
text
Timestamp, IP Address, Sensor Value
2025-11-23 16:53:28.616, 192.168.137.172, 406


---

## Web Server Dashboard Screenshots

### Dashboard Overview
<img width="1422" height="984" alt="Screenshot 2025-12-05 223120" src="https://github.com/user-attachments/assets/ee00d063-28f0-4943-84a5-1b7ec8516e2b" />


### Master Status & Device Activity
<img width="1455" height="985" alt="Screenshot 2025-12-05 223049" src="https://github.com/user-attachments/assets/927d41a7-79dc-454e-92f0-76507d3ccdb1" />


### Data Logs
<img width="1544" height="995" alt="Screenshot 2025-12-05 223132" src="https://github.com/user-attachments/assets/061115df-c32a-43d4-86df-a1aa7628b80c" />
