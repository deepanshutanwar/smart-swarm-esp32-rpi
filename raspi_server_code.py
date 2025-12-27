
from flask import Flask, render_template_string, jsonify, request, send_file
from flask_cors import CORS
from datetime import datetime
import threading
import time
import os
import csv

app = Flask(__name__)
CORS(app)

# ============================================================================
# GLOBAL STATE
# ============================================================================

latest_data = {
    'timestamp': None,
    'nodes': {},
    'current_master': None,
    'graph_data': [],
    'master_durations': {},
    'network_info': {},
    'last_update': None,
    'logging_active': False,
    'session_start': None,
    'current_log_file': None
}

data_lock = threading.Lock()

# Log file management
local_log_directory = 'webserver_logs'
os.makedirs(local_log_directory, exist_ok=True)
rpi_log_files_directory = None

# Session-based logging
current_log_file = None
current_log_writer = None
session_data_buffer = []
all_masters_in_session = set()
session_start_time = None

# ============================================================================
# SESSION-BASED LOGGING FUNCTIONS
# ============================================================================

def start_local_logging_session():
    """Start a new logging session when button is pressed (web server side)"""
    global current_log_file, current_log_writer, session_data_buffer
    global all_masters_in_session, session_start_time
    
    # Close any existing file
    if current_log_file and not current_log_file.closed:
        current_log_file.close()
    
    # Create new log file with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"swarm_log_{timestamp}.csv"
    filepath = os.path.join(local_log_directory, filename)
    
    # Open new file
    current_log_file = open(filepath, 'w', newline='')
    current_log_writer = csv.writer(current_log_file)
    
    # Write header
    current_log_file.write(f"# Session started: {datetime.now().isoformat()}\n")
    current_log_writer.writerow([
        'timestamp', 'node_ip', 'node_name', 'sensor_value', 
        'is_master', 'master_duration_seconds', 'session_elapsed_seconds'
    ])
    current_log_file.flush()
    
    # Reset session tracking
    session_data_buffer.clear()
    all_masters_in_session = set()
    session_start_time = time.time()
    
    print(f"\n{'='*60}")
    print(f"WEB SERVER: LOGGING SESSION STARTED")
    print(f"File: {filename}")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")
    
    return filename

def stop_local_logging_session():
    """Stop current logging session and write summary"""
    global current_log_file, current_log_writer
    
    if not current_log_file or current_log_file.closed:
        return
    
    # Calculate session duration
    session_duration = time.time() - session_start_time if session_start_time else 0
    
    # Write summary section
    current_log_file.write(f"\n# Session ended: {datetime.now().isoformat()}\n")
    current_log_file.write(f"# Total session duration: {session_duration:.2f} seconds\n")
    current_log_file.write(f"\n# MASTER SUMMARY (Devices that became MASTER):\n")
    current_log_file.write("# IP Address,Name,Total Duration (seconds)\n")
    
    # Calculate master durations from session data
    master_durations = calculate_master_durations_from_buffer()
    
    for ip in sorted(all_masters_in_session):
        # Find node name from session data
        node_name = "UNKNOWN"
        for point in session_data_buffer:
            if point['ip'] == ip:
                node_name = point['name']
                break
        
        duration = master_durations.get(ip, 0.0)
        current_log_file.write(f"# {ip},{node_name},{duration:.2f}\n")
    
    # Close file
    current_log_file.close()
    
    filename = os.path.basename(current_log_file.name)
    
    print(f"\n{'='*60}")
    print(f"WEB SERVER: LOGGING SESSION STOPPED")
    print(f"File: {filename}")
    print(f"  Duration: {session_duration:.2f} seconds")
    print(f" Masters: {len(all_masters_in_session)}")
    for ip in sorted(all_masters_in_session):
        node_name = "UNKNOWN"
        for point in session_data_buffer:
            if point['ip'] == ip:
                node_name = point['name']
                break
        duration = master_durations.get(ip, 0.0)
        print(f"   • {node_name} ({ip}): {duration:.2f}s")
    print(f"{'='*60}\n")
    
    current_log_file = None
    current_log_writer = None

def log_data_point_to_file(data_point):
    """Write a single data point to the current log file"""
    global current_log_writer, current_log_file
    
    if not current_log_file or current_log_file.closed:
        return
    
    try:
        # Store in buffer for summary calculation
        session_data_buffer.append(data_point)
        
        # Track all masters
        if data_point['is_master']:
            all_masters_in_session.add(data_point['ip'])
        
        # Calculate session elapsed time
        session_elapsed = time.time() - session_start_time if session_start_time else 0
        
        # Calculate master duration up to this point
        master_duration = calculate_master_duration_for_ip(data_point['ip'])
        
        # Write row
        current_log_writer.writerow([
            datetime.fromtimestamp(data_point['timestamp']).isoformat(),
            data_point['ip'],
            data_point['name'],
            data_point['value'],
            data_point['is_master'],
            f"{master_duration:.2f}",
            f"{session_elapsed:.2f}"
        ])
        current_log_file.flush()
        
    except Exception as e:
        print(f" Error writing to log file: {e}")

def calculate_master_duration_for_ip(target_ip):
    """Calculate how long a specific IP has been master so far in this session"""
    duration = 0.0
    last_master_ip = None
    last_time = None
    
    for point in session_data_buffer:
        if point['is_master']:
            if last_master_ip == point['ip'] and last_time:
                duration_segment = point['timestamp'] - last_time
                if point['ip'] == target_ip:
                    duration += duration_segment
            
            last_master_ip = point['ip']
            last_time = point['timestamp']
    
    return duration

def calculate_master_durations_from_buffer():
    """Calculate total master duration for all IPs from session buffer"""
    master_durations = {}
    last_master_ip = None
    last_time = None
    
    for point in session_data_buffer:
        if point['is_master']:
            if last_master_ip and last_time:
                duration = point['timestamp'] - last_time
                if last_master_ip not in master_durations:
                    master_durations[last_master_ip] = 0.0
                master_durations[last_master_ip] += duration
            
            last_master_ip = point['ip']
            last_time = point['timestamp']
    
    # Add final duration if session ended with a master
    if last_master_ip and last_time and session_start_time:
        final_duration = time.time() - last_time
        if last_master_ip not in master_durations:
            master_durations[last_master_ip] = 0.0
        master_durations[last_master_ip] += final_duration
    
    return master_durations

# ============================================================================
# LOG FILE ANALYSIS FUNCTIONS
# ============================================================================

def analyze_log_file(filepath):
    """Analyze a log file and return visualization data"""
    try:
        data_points = []
        master_summary = {}
        all_nodes = {}
        
        with open(filepath, 'r') as f:
            lines = f.readlines()
            
            # Parse data rows
            in_data_section = False
            for line in lines:
                line = line.strip()
                
                # Skip comments and headers
                if line.startswith('#') or not line:
                    continue
                
                # Check for header
                if line.startswith('timestamp,'):
                    in_data_section = True
                    continue
                
                if in_data_section and ',' in line:
                    parts = line.split(',')
                    if len(parts) >= 5:
                        try:
                            timestamp_str = parts[0]
                            ip = parts[1]
                            name = parts[2]
                            value = int(parts[3])
                            is_master = parts[4].lower() == 'true'
                            
                            # Parse timestamp
                            try:
                                timestamp = datetime.fromisoformat(timestamp_str).timestamp()
                            except:
                                continue
                            
                            data_points.append({
                                'timestamp': timestamp,
                                'ip': ip,
                                'name': name,
                                'value': value,
                                'is_master': is_master
                            })
                            
                            if ip not in all_nodes:
                                all_nodes[ip] = name
                                
                        except (ValueError, IndexError):
                            continue
        
        if not data_points:
            return None
        
        data_points.sort(key=lambda x: x['timestamp'])
        
        # Calculate master durations from raw data
        master_info = {}
        for ip, name in all_nodes.items():
            master_info[ip] = {
                'name': name,
                'duration': 0.0,
                'count': 0
            }
        
        last_master_ip = None
        last_time = None
        
        for point in data_points:
            if point['is_master']:
                if last_master_ip == point['ip'] and last_time:
                    duration = point['timestamp'] - last_time
                    master_info[point['ip']]['duration'] += duration
                    master_info[point['ip']]['count'] += 1
                
                last_master_ip = point['ip']
                last_time = point['timestamp']
        
        # Format master durations by IP
        master_durations = {}
        for ip, info in master_info.items():
            if info['duration'] > 0:
                master_durations[f"{info['name']} ({ip})"] = {
                    'ip': ip,
                    'name': info['name'],
                    'duration': info['duration'],
                    'count': info['count']
                }
        
        return {
            'data_points': data_points,
            'master_durations': master_durations,
            'start_time': datetime.fromtimestamp(data_points[0]['timestamp']).isoformat(),
            'end_time': datetime.fromtimestamp(data_points[-1]['timestamp']).isoformat(),
            'duration': data_points[-1]['timestamp'] - data_points[0]['timestamp'],
            'total_points': len(data_points),
            'num_masters': len(master_durations)
        }
    
    except Exception as e:
        print(f"Error analyzing log: {e}")
        return None

# ============================================================================
# HTML TEMPLATE - PART 1
# ============================================================================

HTML_PART1 = '''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>RPi Light Swarm Monitor</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            overflow: hidden;
        }
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            text-align: center;
        }
        .header h1 { font-size: 2.5em; margin-bottom: 10px; }
        .header p { font-size: 1.1em; opacity: 0.9; }
        .status-bar {
            background: #f8f9fa;
            padding: 15px 30px;
            border-bottom: 2px solid #e0e0e0;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 10px;
        }
        .status-dot {
            width: 12px;
            height: 12px;
            border-radius: 50%;
            background: #dc3545;
            display: inline-block;
            margin-right: 10px;
            animation: pulse 2s infinite;
        }
        .status-dot.connected {
            background: #28a745;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        .logging-status {
            padding: 8px 20px;
            border-radius: 20px;
            font-weight: bold;
            font-size: 0.9em;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .logging-status.active {
            background: #28a745;
            color: white;
            animation: recording 1.5s infinite;
        }
        .logging-status.inactive {
            background: #6c757d;
            color: white;
        }
        @keyframes recording {
            0%, 100% { box-shadow: 0 0 0 0 rgba(40, 167, 69, 0.7); }
            50% { box-shadow: 0 0 0 10px rgba(40, 167, 69, 0); }
        }
        .tabs {
            display: flex;
            background: #f8f9fa;
            border-bottom: 2px solid #e0e0e0;
            overflow-x: auto;
        }
        .tab {
            padding: 15px 30px;
            cursor: pointer;
            border: none;
            background: transparent;
            font-weight: bold;
            color: #666;
            transition: all 0.3s;
            white-space: nowrap;
        }
        .tab:hover {
            background: #e9ecef;
        }
        .tab.active {
            background: white;
            color: #667eea;
            border-bottom: 3px solid #667eea;
        }
        .tab-content {
            display: none;
            padding: 20px;
        }
        .tab-content.active {
            display: block;
        }
        .master-section {
            background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
            color: white;
            padding: 30px;
            text-align: center;
            margin: 20px;
            border-radius: 15px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
        }
        .master-section h2 {
            font-size: 1.8em;
            margin-bottom: 15px;
        }
        .master-section .value {
            font-size: 3em;
            font-weight: bold;
        }
        .master-section .ip {
            font-size: 1em;
            opacity: 0.9;
            margin-top: 10px;
        }
        .nodes-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 20px;
            padding: 20px;
        }
        .node-card {
            background: white;
            border-radius: 15px;
            padding: 20px;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
            border-left: 5px solid #ccc;
            transition: transform 0.2s;
        }
        .node-card:hover {
            transform: translateY(-5px);
            box-shadow: 0 10px 25px rgba(0,0,0,0.15);
        }
        .node-card.master {
            background: linear-gradient(135deg, #ffeaa7 0%, #fdcb6e 100%);
            border-left: 5px solid #f39c12;
        }
        .node-card h3 {
            margin-bottom: 10px;
            font-size: 1.3em;
        }
        .node-card .value {
            font-size: 2.5em;
            font-weight: bold;
            margin: 10px 0;
        }
        .node-card .ip {
            font-size: 0.85em;
            color: #666;
        }
        .chart-container {
            background: white;
            border-radius: 15px;
            padding: 25px;
            margin: 20px;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
        }
        .chart-container h3 {
            margin-bottom: 20px;
            color: #333;
            font-size: 1.3em;
        }
        .log-selector {
            background: white;
            border-radius: 15px;
            padding: 25px;
            margin: 20px;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
        }
        .log-selector label {
            display: block;
            margin-bottom: 10px;
            font-weight: bold;
            color: #333;
        }
        .log-selector select {
            width: 100%;
            padding: 12px;
            border: 2px solid #e0e0e0;
            border-radius: 8px;
            font-size: 1em;
            background: white;
            cursor: pointer;
        }
        .log-selector select:focus {
            outline: none;
            border-color: #667eea;
        }
        .btn {
            padding: 12px 24px;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-weight: bold;
            margin: 5px;
            text-decoration: none;
            display: inline-block;
            transition: all 0.3s;
        }
        .btn-primary {
            background: #667eea;
            color: white;
        }
        .btn-primary:hover {
            background: #5568d3;
            transform: translateY(-2px);
            box-shadow: 0 5px 15px rgba(102, 126, 234, 0.4);
        }
        canvas {
            max-height: 400px;
        }
        .log-file-item {
            padding: 15px;
            border-bottom: 1px solid #eee;
            display: flex;
            justify-content: space-between;
            align-items: center;
            transition: background 0.2s;
        }
        .log-file-item:hover {
            background: #f8f9fa;
        }
        .log-file-item:last-child {
            border-bottom: none;
        }
        .info-box {
            background: #e3f2fd;
            border-left: 4px solid #2196f3;
            padding: 20px;
            margin: 20px;
            border-radius: 8px;
            line-height: 1.6;
        }
        .info-box strong {
            color: #1976d2;
        }
        #logInfo {
            background: #f8f9fa;
            padding: 20px;
            border-radius: 8px;
            margin-top: 15px;
        }
        #logInfo p {
            margin: 8px 0;
            font-size: 0.95em;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🔆 RPi Light Swarm Monitor</h1>
            <p>Button-Based Session Logging System</p>
        </div>
        <div class="status-bar">
            <div>
                <span class="status-dot" id="statusDot"></span>
                <span id="statusText">Waiting for connection...</span>
            </div>
            <div>
                <span class="logging-status inactive" id="loggingStatus">
                    ⏹️ NOT LOGGING
                </span>
            </div>
        </div>
        <div class="tabs">
            <button class="tab active" onclick="switchTab(event, 'realtime')">📊 Real-time</button>
            <button class="tab" onclick="switchTab(event, 'viewer')">📈 Log Viewer</button>
            <button class="tab" onclick="switchTab(event, 'logs')">📁 Log Files</button>
        </div>
'''


HTML_PART2 = '''
        <div id="realtime" class="tab-content active">
            <div class="info-box">
                <strong>🔘 How to Use:</strong><br>
                • Press button on Raspberry Pi <strong>ONCE</strong> to START logging (creates new file with timestamp)<br>
                • Press button <strong>AGAIN</strong> to STOP logging (saves file with master summary)<br>
                • Each log file contains: all devices that became masters (by IP), duration from beginning, and raw data
            </div>
            
            <div class="master-section" id="masterSection">
                <h2 id="masterTitle">No MASTER</h2>
                <div class="value" id="masterValue">--</div>
                <div class="ip" id="masterIP"></div>
            </div>
            
            <div class="nodes-grid" id="nodesGrid">
                <p style="text-align: center; padding: 40px; color: #999;">
                    Waiting for data from ESP32 nodes...
                </p>
            </div>
            
            <div class="chart-container">
                <h3>📉 Photocell Data (Last 30 seconds)</h3>
                <canvas id="sensorChart"></canvas>
            </div>
            
            <div class="chart-container">
                <h3>⏱️ Master Duration (Current Session)</h3>
                <canvas id="durationChart"></canvas>
            </div>
        </div>
        
        <div id="viewer" class="tab-content">
            <div class="info-box">
                <strong>📊 Log File Analysis:</strong><br>
                Select any saved log file to visualize the complete session data including:<br>
                • <strong>All devices that became masters</strong> (identified by IP address)<br>
                • <strong>How long each device was master</strong> (measured from session beginning)<br>
                • <strong>Raw photocell data from each master</strong> during their reign<br>
                • Timeline showing master transitions and sensor values
            </div>
            
            <div class="log-selector">
                <label>📁 Select Log File to Analyze:</label>
                <select id="logFileSelect" onchange="loadLogVisualization()">
                    <option value="">-- Choose a log file --</option>
                </select>
                <div id="logInfo" style="display: none;">
                    <p><strong>📅 Start Time:</strong> <span id="logStart"></span></p>
                    <p><strong>⏱️ Session Duration:</strong> <span id="logDuration"></span></p>
                    <p><strong>📊 Data Points:</strong> <span id="logPoints"></span></p>
                    <p><strong>👑 Masters in Session:</strong> <span id="logMasters"></span></p>
                </div>
            </div>
            
            <div class="chart-container">
                <h3>📈 Sensor Values Over Time (Session Timeline)</h3>
                <canvas id="logSensorChart"></canvas>
            </div>
            
            <div class="chart-container">
                <h3>👑 Master Duration by Device (IP Address)</h3>
                <canvas id="logDurationChart"></canvas>
            </div>
        </div>
        
        <div id="logs" class="tab-content">
            <div class="info-box">
                <strong>📁 Log Files:</strong><br>
                Each log file represents one button-press session (START to STOP).<br>
                Files are named: <code>swarm_log_YYYYMMDD_HHMMSS.csv</code><br>
                Contains: timestamps, IP addresses, node names, sensor values, master status, and duration summary.
            </div>
            
            <button class="btn btn-primary" onclick="loadLogFiles()" style="margin: 20px;">
                🔄 Refresh List
            </button>
            
            <div style="padding: 20px;">
                <h3 style="margin-bottom: 15px;">💾 Local Web Server Logs</h3>
                <div id="localLogsList">
                    <p style="color: #999; padding: 20px;">Loading...</p>
                </div>
                
                <h3 style="margin-top: 30px; margin-bottom: 15px;">🔴 Raspberry Pi Logs</h3>
                <div id="rpiLogsList">
                    <p style="color: #999; padding: 20px;">Loading...</p>
                </div>
            </div>
        </div>
    </div>

    <script>
        let sensorChart, durationChart, logSensorChart, logDurationChart;
        
        const colors = {
            'RED': '#e74c3c',
            'BLUE': '#3498db',
            'GREEN': '#2ecc71',
            'YELLOW': '#f1c40f',
            'PURPLE': '#9b59b6'
        };
        
        function switchTab(event, tabName) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            event.target.classList.add('active');
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            document.getElementById(tabName).classList.add('active');
            
            if (tabName === 'logs') loadLogFiles();
            if (tabName === 'viewer') populateLogSelector();
        }
        
        function initCharts() {
            const chartDefaults = {
                responsive: true,
                maintainAspectRatio: true,
                interaction: {
                    intersect: false,
                    mode: 'index'
                }
            };
            
            sensorChart = new Chart(document.getElementById('sensorChart'), {
                type: 'line',
                data: { datasets: [] },
                options: {
                    ...chartDefaults,
                    scales: {
                        x: {
                            type: 'linear',
                            title: { display: true, text: 'Seconds Ago' },
                            reverse: true,
                            min: 0,
                            max: 30
                        },
                        y: {
                            title: { display: true, text: 'Sensor Value' },
                            min: 0,
                            max: 4095
                        }
                    }
                }
            });
            
            durationChart = new Chart(document.getElementById('durationChart'), {
                type: 'bar',
                data: {
                    labels: [],
                    datasets: [{
                        label: 'Duration (seconds)',
                        data: [],
                        backgroundColor: []
                    }]
                },
                options: {
                    ...chartDefaults,
                    scales: {
                        y: {
                            beginAtZero: true,
                            title: { display: true, text: 'Seconds as Master' }
                        }
                    }
                }
            });
            
            logSensorChart = new Chart(document.getElementById('logSensorChart'), {
                type: 'line',
                data: { datasets: [] },
                options: {
                    ...chartDefaults,
                    scales: {
                        x: {
                            type: 'linear',
                            title: { display: true, text: 'Time (seconds from start)' }
                        },
                        y: {
                            title: { display: true, text: 'Sensor Value' },
                            min: 0,
                            max: 4095
                        }
                    }
                }
            });
            
            logDurationChart = new Chart(document.getElementById('logDurationChart'), {
                type: 'bar',
                data: {
                    labels: [],
                    datasets: [{
                        label: 'Master Duration (seconds)',
                        data: [],
                        backgroundColor: []
                    }]
                },
                options: {
                    ...chartDefaults,
                    indexAxis: 'y',
                    scales: {
                        x: {
                            beginAtZero: true,
                            title: { display: true, text: 'Duration (seconds)' }
                        }
                    },
                    plugins: {
                        tooltip: {
                            callbacks: {
                                label: function(context) {
                                    return 'Duration: ' + context.parsed.x.toFixed(2) + ' seconds';
                                }
                            }
                        }
                    }
                }
            });
        }
        
        async function populateLogSelector() {
            try {
                const res = await fetch('/api/logs/all');
                const data = await res.json();
                const select = document.getElementById('logFileSelect');
                select.innerHTML = '<option value="">-- Choose a log file --</option>';
                
                if (data.local && data.local.length > 0) {
                    const g = document.createElement('optgroup');
                    g.label = '💾 Local Logs';
                    data.local.forEach(f => {
                        const o = document.createElement('option');
                        o.value = `local:${f.name}`;
                        o.textContent = `${f.name} (${f.size})`;
                        g.appendChild(o);
                    });
                    select.appendChild(g);
                }
                
                if (data.rpi && data.rpi.length > 0) {
                    const g = document.createElement('optgroup');
                    g.label = '🔴 RPi Logs';
                    data.rpi.forEach(f => {
                        const o = document.createElement('option');
                        o.value = `rpi:${f.name}`;
                        o.textContent = `${f.name} (${f.size})`;
                        g.appendChild(o);
                    });
                    select.appendChild(g);
                }
            } catch (error) {
                console.error('Error loading log files:', error);
            }
        }
        
        async function loadLogVisualization() {
            const val = document.getElementById('logFileSelect').value;
            if (!val) {
                document.getElementById('logInfo').style.display = 'none';
                return;
            }
            
            try {
                const [source, filename] = val.split(':');
                const res = await fetch(`/api/logs/analyze/${source}/${filename}`);
                const analysis = await res.json();
                
                if (analysis.error) {
                    alert('Error: ' + analysis.error);
                    return;
                }
                
                document.getElementById('logStart').textContent = 
                    new Date(analysis.start_time).toLocaleString();
                document.getElementById('logDuration').textContent = 
                    analysis.duration.toFixed(2) + ' seconds';
                document.getElementById('logPoints').textContent = 
                    analysis.total_points.toLocaleString();
                document.getElementById('logMasters').textContent = 
                    analysis.num_masters + ' device(s)';
                document.getElementById('logInfo').style.display = 'block';
                
                const nodeData = {};
                const startTime = analysis.data_points[0].timestamp;
                
                analysis.data_points.forEach(p => {
                    if (!nodeData[p.name]) nodeData[p.name] = [];
                    nodeData[p.name].push({
                        x: p.timestamp - startTime,
                        y: p.value
                    });
                });
                
                logSensorChart.data.datasets = Object.keys(nodeData).map(name => ({
                    label: name,
                    data: nodeData[name],
                    borderColor: colors[name] || '#999',
                    backgroundColor: (colors[name] || '#999') + '33',
                    borderWidth: 2,
                    tension: 0.4
                }));
                logSensorChart.update();
                
                const labels = [];
                const durations = [];
                const bgColors = [];
                
                Object.entries(analysis.master_durations).forEach(([label, info]) => {
                    labels.push(label);
                    durations.push(info.duration);
                    bgColors.push(colors[info.name] || '#999');
                });
                
                logDurationChart.data.labels = labels;
                logDurationChart.data.datasets[0].data = durations;
                logDurationChart.data.datasets[0].backgroundColor = bgColors;
                logDurationChart.update();
                
            } catch (error) {
                console.error('Error loading visualization:', error);
                alert('Error loading log file visualization');
            }
        }
        
        function updateUI(data) {
            document.getElementById('statusDot').classList.add('connected');
            document.getElementById('statusText').textContent = 'Connected to RPi';
            
            const loggingStatus = document.getElementById('loggingStatus');
            if (data.logging_active) {
                loggingStatus.innerHTML = '🔴 RECORDING';
                loggingStatus.className = 'logging-status active';
            } else {
                loggingStatus.innerHTML = '⏹️ NOT LOGGING';
                loggingStatus.className = 'logging-status inactive';
            }
            
            if (data.current_master) {
                const m = data.current_master;
                document.getElementById('masterTitle').textContent = 
                    `MASTER: ${m.name}`;
                document.getElementById('masterValue').textContent = m.value;
                document.getElementById('masterIP').textContent = `IP: ${m.ip}`;
            } else {
                document.getElementById('masterTitle').textContent = 'No MASTER';
                document.getElementById('masterValue').textContent = '--';
                document.getElementById('masterIP').textContent = '';
            }
            
            if (data.nodes && Object.keys(data.nodes).length > 0) {
                let html = '';
                for (const [ip, node] of Object.entries(data.nodes)) {
                    const masterClass = node.is_master ? 'master' : '';
                    const masterBadge = node.is_master ? '👑 ' : '';
                    html += `
                        <div class="node-card ${masterClass}">
                            <h3>${masterBadge}${node.name}</h3>
                            <div class="value">${node.value}</div>
                            <div class="ip">${ip}</div>
                        </div>
                    `;
                }
                document.getElementById('nodesGrid').innerHTML = html;
            }
            
            if (data.graph_data && data.graph_data.length > 0) {
                const nodeData = {};
                const now = Date.now() / 1000;
                
                data.graph_data.forEach(p => {
                    if (!nodeData[p.name]) nodeData[p.name] = [];
                    nodeData[p.name].push({
                        x: now - p.timestamp,
                        y: p.value
                    });
                });
                
                sensorChart.data.datasets = Object.keys(nodeData).map(name => ({
                    label: name,
                    data: nodeData[name],
                    borderColor: colors[name] || '#999',
                    backgroundColor: (colors[name] || '#999') + '33',
                    borderWidth: 2,
                    tension: 0.4
                }));
                sensorChart.update('none');
            }
            
            if (data.master_durations) {
                durationChart.data.labels = Object.keys(data.master_durations);
                durationChart.data.datasets[0].data = Object.values(data.master_durations);
                durationChart.data.datasets[0].backgroundColor = 
                    Object.keys(data.master_durations).map(n => colors[n] || '#999');
                durationChart.update('none');
            }
        }
        
        async function loadLogFiles() {
            try {
                const res = await fetch('/api/logs/all');
                const data = await res.json();
                
                let localHtml = '';
                if (data.local && data.local.length > 0) {
                    data.local.forEach(f => {
                        localHtml += `
                            <div class="log-file-item">
                                <div>
                                    <strong>${f.name}</strong><br>
                                    <small>${f.size} • Modified: ${f.modified}</small>
                                </div>
                                <a href="/api/logs/local/${f.name}" 
                                   class="btn btn-primary" download>
                                    ⬇️ Download
                                </a>
                            </div>
                        `;
                    });
                } else {
                    localHtml = '<p style="padding: 20px; color: #999;">No local logs yet</p>';
                }
                document.getElementById('localLogsList').innerHTML = localHtml;
                
                let rpiHtml = '';
                if (data.rpi && data.rpi.length > 0) {
                    data.rpi.forEach(f => {
                        rpiHtml += `
                            <div class="log-file-item">
                                <div>
                                    <strong>${f.name}</strong><br>
                                    <small>${f.size} • Modified: ${f.modified}</small>
                                </div>
                                <a href="/api/logs/rpi/${f.name}" 
                                   class="btn btn-primary" download>
                                    ⬇️ Download
                                </a>
                            </div>
                        `;
                    });
                } else {
                    rpiHtml = '<p style="padding: 20px; color: #999;">No RPi logs available</p>';
                }
                document.getElementById('rpiLogsList').innerHTML = rpiHtml;
                
            } catch (error) {
                console.error('Error loading log files:', error);
            }
        }
        
        async function fetchData() {
            try {
                const res = await fetch('/api/data');
                const data = await res.json();
                if (data.timestamp) {
                    updateUI(data);
                }
            } catch (error) {
                console.error('Error fetching data:', error);
            }
        }
        
        initCharts();
        setInterval(fetchData, 500);
        fetchData();
        loadLogFiles();
    </script>
</body>
</html>
'''


# ============================================================================
# API ROUTES
# ============================================================================

@app.route('/')
def index():
    """Serve main dashboard"""
    full_html = HTML_PART1 + HTML_PART2
    return render_template_string(full_html)

@app.route('/api/data', methods=['POST'])
def receive_data():
    """Receive data from Raspberry Pi"""
    try:
        data = request.get_json()
        
        # Handle button press signals
        if 'button_action' in data:
            action = data['button_action']
            
            with data_lock:
                if action == 'start':
                    # Start logging session on web server
                    filename = start_local_logging_session()
                    latest_data['logging_active'] = True
                    latest_data['current_log_file'] = filename
                    
                elif action == 'stop':
                    # Stop logging session
                    stop_local_logging_session()
                    latest_data['logging_active'] = False
                    latest_data['current_log_file'] = None
            
            return jsonify({
                'status': 'success',
                'action': action
            }), 200
        
        # Regular data update
        with data_lock:
            latest_data.update(data)
            latest_data['last_update'] = time.time()
            
            # Store RPi log directory path
            global rpi_log_files_directory
            if 'log_directory' in data:
                rpi_log_files_directory = data['log_directory']
            
            # If logging is active, write data points to file
            if latest_data.get('logging_active', False):
                # Process graph_data and write to log file
                if 'graph_data' in data and data['graph_data']:
                    for point in data['graph_data']:
                        # Check if this point is already logged
                        # (simple check: only log if not in buffer or is newer)
                        if not any(
                            p['timestamp'] == point['timestamp'] and 
                            p['ip'] == point['ip'] 
                            for p in session_data_buffer
                        ):
                            log_data_point_to_file(point)
        
        return jsonify({'status': 'success'}), 200
        
    except Exception as e:
        print(f"Error receiving data: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 400

@app.route('/api/data', methods=['GET'])
def get_data():
    """Get current data for dashboard"""
    with data_lock:
        response = latest_data.copy()
        return jsonify(response)

@app.route('/api/status')
def get_status():
    """Check connection status"""
    with data_lock:
        if latest_data['last_update']:
            time_since = time.time() - latest_data['last_update']
            connected = time_since < 5
        else:
            connected = False
            time_since = None
        
        return jsonify({
            'is_connected': connected,
            'time_since_update': time_since,
            'logging_active': latest_data.get('logging_active', False),
            'current_log_file': latest_data.get('current_log_file')
        })

@app.route('/api/logs/all')
def list_all_logs():
    """List all log files from both local and RPi"""
    try:
        result = {'local': [], 'rpi': []}
        
        # Get local logs
        if os.path.exists(local_log_directory):
            for filename in os.listdir(local_log_directory):
                if filename.startswith('swarm_log_') and filename.endswith('.csv'):
                    filepath = os.path.join(local_log_directory, filename)
                    stat = os.stat(filepath)
                    result['local'].append({
                        'name': filename,
                        'size': f"{stat.st_size / 1024:.1f} KB",
                        'modified': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                    })
        
        result['local'].sort(key=lambda x: x['modified'], reverse=True)
        
        # Get RPi logs
        if rpi_log_files_directory and os.path.exists(rpi_log_files_directory):
            for filename in os.listdir(rpi_log_files_directory):
                if filename.startswith('swarm_log_') and filename.endswith('.csv'):
                    filepath = os.path.join(rpi_log_files_directory, filename)
                    try:
                        stat = os.stat(filepath)
                        result['rpi'].append({
                            'name': filename,
                            'size': f"{stat.st_size / 1024:.1f} KB",
                            'modified': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                        })
                    except:
                        pass
        
        result['rpi'].sort(key=lambda x: x['modified'], reverse=True)
        
        return jsonify(result)
        
    except Exception as e:
        print(f"Error listing logs: {e}")
        return jsonify({'local': [], 'rpi': [], 'error': str(e)})

@app.route('/api/logs/analyze/<source>/<filename>')
def analyze_log_endpoint(source, filename):
    """Analyze a log file and return visualization data"""
    try:
        # Security check
        if not filename.startswith('swarm_log_') or not filename.endswith('.csv'):
            return jsonify({'error': 'Invalid filename'}), 400
        
        if source == 'local':
            filepath = os.path.join(local_log_directory, filename)
        elif source == 'rpi':
            if not rpi_log_files_directory:
                return jsonify({'error': 'RPi directory not available'}), 404
            filepath = os.path.join(rpi_log_files_directory, filename)
        else:
            return jsonify({'error': 'Invalid source'}), 400
        
        if not os.path.exists(filepath):
            return jsonify({'error': 'File not found'}), 404
        
        analysis = analyze_log_file(filepath)
        
        if analysis is None:
            return jsonify({'error': 'Could not analyze log file'}), 500
        
        return jsonify(analysis)
    
    except Exception as e:
        print(f"Error analyzing log: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/logs/local/<filename>')
def download_local_log(filename):
    """Download local log file"""
    try:
        # Security check
        if not filename.startswith('swarm_log_') or not filename.endswith('.csv'):
            return jsonify({'error': 'Invalid filename'}), 400
        
        filepath = os.path.join(local_log_directory, filename)
        
        if not os.path.exists(filepath):
            return jsonify({'error': 'File not found'}), 404
        
        return send_file(filepath, as_attachment=True, download_name=filename)
        
    except Exception as e:
        print(f"Error downloading local log: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/logs/rpi/<filename>')
def download_rpi_log(filename):
    """Download RPi log file"""
    try:
        # Security check
        if not filename.startswith('swarm_log_') or not filename.endswith('.csv'):
            return jsonify({'error': 'Invalid filename'}), 400
        
        if not rpi_log_files_directory:
            return jsonify({'error': 'RPi directory not available'}), 404
        
        filepath = os.path.join(rpi_log_files_directory, filename)
        
        if not os.path.exists(filepath):
            return jsonify({'error': 'File not found'}), 404
        
        return send_file(filepath, as_attachment=True, download_name=filename)
        
    except Exception as e:
        print(f"Error downloading RPi log: {e}")
        return jsonify({'error': str(e)}), 500

# ============================================================================
# MAIN EXECUTION
# ============================================================================

if __name__ == '__main__':
    print("\n" + "="*70)
    print(" RASPBERRY PI LIGHT SWARM - WEB SERVER")
    print(" BUTTON-BASED SESSION LOGGING SYSTEM")
    print("="*70)
    
    print("\n SETUP INSTRUCTIONS:")
    print("="*70)
    print("1. Find your laptop's IP address:")
    print("   • Windows: Run 'ipconfig' in Command Prompt")
    print("   • Mac/Linux: Run 'ifconfig' or 'ip addr' in Terminal")
    print("\n2. Update Raspberry Pi code:")
    print("   • Open raspi_monitor.py")
    print("   • Change WEB_SERVER_URL to: http://YOUR_LAPTOP_IP:5000")
    print("\n3. Access the dashboard:")
    print("   • From this computer: http://localhost:5000")
    print("   • From other devices: http://YOUR_LAPTOP_IP:5000")
    print("="*70)
    
    print("\n FEATURES:")
    print("="*70)
    print(" Button Operation:")
    print("   • Press button ONCE  → START logging (creates new file)")
    print("   • Press button AGAIN → STOP logging (saves with summary)")
    print("\n Log File Contents:")
    print("   • All devices that became masters (IP addresses)")
    print("   • How long each device was master (from beginning)")
    print("   • Raw data from each master")
    print("   • Session summary at end of file")
    print("\n Web Dashboard:")
    print("   • Real-time monitoring with live charts")
    print("   • Log file viewer (select any file to visualize)")
    print("   • Bar chart showing masters by IP with duration")
    print("   • Photocell data timeline")
    print("   • Download any log file")
    print(f"\n Logs Directory: {os.path.abspath(local_log_directory)}")
    print("="*70)
    
    print("\n LOG FILE FORMAT:")
    print("="*70)
    print("timestamp,node_ip,node_name,sensor_value,is_master,master_duration_seconds,session_elapsed_seconds")
    print("\nExample:")
    print("2024-12-05T10:30:01,192.168.137.35,RED,2048,True,5.50,10.00")
    print("2024-12-05T10:30:02,192.168.137.34,BLUE,1024,False,0.00,11.00")
    print("\nSummary section (at end):")
    print("# MASTER SUMMARY (Devices that became MASTER):")
    print("# IP Address,Name,Total Duration (seconds)")
    print("# 192.168.137.35,RED,120.50")
    print("="*70)
    
    print("\n STARTING SERVER...")
    print("="*70)
    print("Server will run on: http://0.0.0.0:5000")
    print("Press Ctrl+C to stop the server")
    print("="*70 + "\n")
    
    try:
        app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
    except KeyboardInterrupt:
        print("\n\n Shutting down server...")
        
        # Close any open log file
        if current_log_file and not current_log_file.closed:
            print(" Closing active log file...")
            stop_local_logging_session()
        
        print("✓ Server stopped")
        print("✓ All logs saved\n")
    except Exception as e:
        print(f"\n\n Server error: {e}\n")
        import traceback
        traceback.print_exc()