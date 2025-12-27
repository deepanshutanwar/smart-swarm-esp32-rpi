import socket
import time
import threading
import requests
import subprocess
import signal
from collections import deque
from datetime import datetime
import atexit
import os

# GPIO imports with error handling
try:
    from gpiozero import LED, Button
    from gpiozero.pins.pigpio import PiGPIOFactory
    GPIO_AVAILABLE = True
    print("gpiozero imported successfully")
except ImportError:
    print("gpiozero not available - running in simulation mode")
    GPIO_AVAILABLE = False

WEB_SERVER_URL = "http://10.8.31.157:5000" 
WEB_SERVER_ENDPOINT = f"{WEB_SERVER_URL}/api/data"

# Network configuration
LOCAL_IP = "192.168.1.10"  # This Raspberry Pi's IP address
UDP_PORT = 4210            # Port for receiving UDP messages from ESP32
BROADCAST_IP = "192.168.1.255"  # Your network's broadcast address

# Button and LED configuration
BUTTON_PIN = 24  # GPIO pin for reset button
YELLOW_LED_PIN = 18  # GPIO pin for status/reset LED

# Node mapping - Map ESP32 IP addresses to LED names and GPIO pins
# Format: "ESP32_IP": ("COLOR_NAME", GPIO_PIN)
ip_led_map = {
    "192.168.137.35": ("RED", 17),
    "192.168.137.34": ("BLUE", 27),
    "192.168.137.165": ("GREEN", 22)
}

# Data retention settings
TIME_WINDOW = 30  # seconds - how long to keep data in graphs
MAX_DATA_POINTS = 1000  # maximum number of data points to store

# LED Matrix Graph Settings
GRAPH_TIME_WINDOW = 30  # 30 seconds of data to display
ROW_TIME = 30.0 / 8.0   # Each row represents 3.75 seconds (30s / 8 rows)
NUM_ROWS = 8            # 8 rows for 8x8 matrix

# Matrix column layout: [Dev0 Col0, Dev0 Col1, BLANK, Dev1 Col0, Dev1 Col1, BLANK, Dev2 Col0, Dev2 Col1]
DEVICE_COLUMNS = {
    0: [0, 1],    # First device uses columns 0-1
    1: [3, 4],    # Second device uses columns 3-4
    2: [6, 7]     # Third device uses columns 6-7
}
BLANK_COLUMNS = [2, 5]  # Columns 2 and 5 are always blank (spacers)

# ============================================================================
# LED MATRIX CONFIGURATION AND INITIALIZATION
# ============================================================================

MATRIX_ENABLED = False
matrix_device = None
matrix_imports_available = False

try:
    import spidev
    matrix_imports_available = True
    print("✓ LED Matrix libraries imported successfully")
except ImportError as e:
    print(f"⚠ LED Matrix libraries not available: {e}")
    print("  Install with: pip3 install spidev")
    matrix_imports_available = False

class MAX7219:
    """Direct SPI control for MAX7219 LED matrix"""
    # MAX7219 Registers
    REG_NOOP = 0x00
    REG_DIGIT0 = 0x01
    REG_DIGIT1 = 0x02
    REG_DIGIT2 = 0x03
    REG_DIGIT3 = 0x04
    REG_DIGIT4 = 0x05
    REG_DIGIT5 = 0x06
    REG_DIGIT6 = 0x07
    REG_DIGIT7 = 0x08
    REG_DECODEMODE = 0x09
    REG_INTENSITY = 0x0A
    REG_SCANLIMIT = 0x0B
    REG_SHUTDOWN = 0x0C
    REG_DISPLAYTEST = 0x0F
    
    def __init__(self, bus=0, device=0):
        self.spi = spidev.SpiDev()
        self.spi.open(bus, device)
        self.spi.max_speed_hz = 1000000
        self.spi.mode = 0
        
        # Initialize display
        self.write_register(self.REG_SCANLIMIT, 0x07)
        self.write_register(self.REG_DECODEMODE, 0x00)
        self.write_register(self.REG_DISPLAYTEST, 0x00)
        self.write_register(self.REG_INTENSITY, 0x08)
        self.write_register(self.REG_SHUTDOWN, 0x01)
        self.clear()
        
    def write_register(self, register, data):
        """Write data to a register"""
        self.spi.xfer2([register, data])
        
    def clear(self):
        """Clear all LEDs"""
        for row in range(8):
            self.write_register(self.REG_DIGIT0 + row, 0x00)
    
    def set_row(self, row, value):
        """Set a row (0-7) to a byte value (0-255)"""
        if 0 <= row <= 7:
            self.write_register(self.REG_DIGIT0 + row, value)
    
    def display_row_graph(self, device_row_data):
        """Display time-series graph where each row represents a time slice"""
        if not hasattr(self, 'row_cache'):
            self.row_cache = [0] * 8
        
        self.row_cache = [0] * 8
        
        for row in range(8):
            row_byte = 0
            
            for device_idx in range(3):
                if device_idx in device_row_data and row < len(device_row_data[device_idx]):
                    value = device_row_data[device_idx][row]
                    cols = DEVICE_COLUMNS[device_idx]
                    
                    if value >= 1:
                        row_byte |= (1 << cols[0])
                    if value >= 2:
                        row_byte |= (1 << cols[1])
            
            self.row_cache[row] = row_byte
        
        for row in range(8):
            self.set_row(row, self.row_cache[row])
    
    def close(self):
        """Close SPI connection"""
        self.clear()
        self.spi.close()

def init_led_matrix():
    """Initialize LED matrix with comprehensive error checking"""
    global matrix_device, MATRIX_ENABLED
    
    if not matrix_imports_available:
        print("LED Matrix: Libraries not available")
        MATRIX_ENABLED = False
        return False
    
    print("\n" + "="*60)
    print("LED MATRIX INITIALIZATION - TIME-SERIES GRAPH MODE")
    print("="*60)
    
    try:
        if not os.path.exists('/dev/spidev0.0'):
            print("⚠ SPI not enabled!")
            MATRIX_ENABLED = False
            return False
        print("SPI device found at /dev/spidev0.0")
    except Exception as e:
        print(f"Could not check SPI status: {e}")
    
    try:
        matrix_device = MAX7219(bus=0, device=0)
        print("MAX7219 device created")
        
        test_data = {
            0: [2, 1, 2, 0, 1, 2, 1, 2],
            1: [1, 2, 1, 2, 0, 1, 2, 1],
            2: [2, 2, 1, 1, 2, 0, 1, 2]
        }
        matrix_device.display_row_graph(test_data)
        print("Test graph displayed on matrix")
        
        time.sleep(2)
        matrix_device.clear()
        
        print("LED Matrix initialized successfully!")
        print(f"Graph Mode: 8 rows × {ROW_TIME:.2f}s = {GRAPH_TIME_WINDOW}s")
        print("="*60 + "\n")
        MATRIX_ENABLED = True
        return True
        
    except Exception as e:
        print(f"Matrix initialization failed: {e}")
        MATRIX_ENABLED = False
        return False

# ============================================================================
# GLOBAL STATE VARIABLES
# ============================================================================

shutdown_flag = threading.Event()

# Current values from each node
node_values = {ip: 0 for ip in ip_led_map}
node_is_master = {ip: False for ip in ip_led_map}
last_update_time = {ip: None for ip in ip_led_map}

# Data storage for graphs
graph_data_unified = deque(maxlen=MAX_DATA_POINTS)
master_duration_data = {ip: 0.0 for ip in ip_led_map}
graph_start_time = None

# Session tracking - Button-based logging
logging_active = False
session_start_time = None
session_data = deque(maxlen=MAX_DATA_POINTS)
all_masters_in_session = set()

# GPIO objects
yellow_led = None
led_objects = {}
reset_button = None

# UDP Socket setup
recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
recv_sock.bind(('', UDP_PORT))

send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

# Logging
log_file = None
log_filename = None

# Track last sent log files list
last_log_files_sent = []

# ============================================================================
# GPIO CLEANUP AND INITIALIZATION
# ============================================================================

def kill_other_gpio_processes():
    """Kill any other Python processes using GPIO"""
    try:
        result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
        lines = result.stdout.split('\n')
        
        current_pid = os.getpid()
        for line in lines:
            if 'python' in line.lower() and str(current_pid) not in line:
                parts = line.split()
                if len(parts) > 1:
                    try:
                        pid = int(parts[1])
                        os.kill(pid, signal.SIGTERM)
                        print(f"  Killed process {pid}")
                    except:
                        pass
        time.sleep(1)
    except Exception as e:
        print(f"  Could not kill processes: {e}")

class MockLED:
    """Mock LED for simulation mode"""
    def __init__(self, pin):
        self.pin = pin
        self._state = False
    
    def on(self):
        self._state = True
        print(f"[SIM] LED {self.pin} ON")
    
    def off(self):
        self._state = False
    
    def close(self):
        pass

class MockButton:
    """Mock Button for simulation mode"""
    def __init__(self, pin):
        self.pin = pin
        self.when_pressed = None
    
    def close(self):
        pass

def cleanup_gpio():
    """Clean up GPIO resources"""
    try:
        if GPIO_AVAILABLE:
            for led in led_objects.values():
                try:
                    led.close()
                except:
                    pass
            try:
                yellow_led.close()
            except:
                pass
            try:
                reset_button.close()
            except:
                pass
            print("GPIO cleanup complete")
    except:
        pass

def init_gpio():
    """Initialize GPIO pins with proper cleanup and factory"""
    global yellow_led, led_objects, reset_button
    
    print("\nInitializing GPIO...")
    kill_other_gpio_processes()
    atexit.register(cleanup_gpio)
    
    if GPIO_AVAILABLE:
        try:
            try:
                factory = PiGPIOFactory()
                print("Using pigpio factory")
            except:
                factory = None
                print("Using default factory")
            
            if factory:
                yellow_led = LED(YELLOW_LED_PIN, pin_factory=factory)
                led_objects = {ip: LED(pin, pin_factory=factory) for ip, (_, pin) in ip_led_map.items()}
                reset_button = Button(BUTTON_PIN, pull_up=True, bounce_time=0.2, 
                                     hold_time=1, pin_factory=factory)
            else:
                yellow_led = LED(YELLOW_LED_PIN)
                led_objects = {ip: LED(pin) for ip, (_, pin) in ip_led_map.items()}
                reset_button = Button(BUTTON_PIN, pull_up=True, bounce_time=0.2, hold_time=1)
            
            reset_button.when_pressed = button_pressed
            
            print(f"GPIO initialized on pin {BUTTON_PIN}")
            print(f"Button state: {'pressed' if reset_button.is_pressed else 'released'}")
            return True
            
        except Exception as e:
            print(f"GPIO init failed: {e}")
            yellow_led = MockLED(YELLOW_LED_PIN)
            led_objects = {ip: MockLED(pin) for ip, (_, pin) in ip_led_map.items()}
            reset_button = MockButton(BUTTON_PIN)
            return False
    else:
        yellow_led = MockLED(YELLOW_LED_PIN)
        led_objects = {ip: MockLED(pin) for ip, (_, pin) in ip_led_map.items()}
        reset_button = MockButton(BUTTON_PIN)
        print("Simulation mode")
        return False

# ============================================================================
# BUTTON-BASED SESSION LOGGING
# ============================================================================

def start_logging_session():
    """Start a new logging session (button press #1)"""
    global logging_active, session_start_time, session_data, all_masters_in_session
    global log_file, log_filename
    
    if log_file and not log_file.closed:
        log_file.close()
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = f"swarm_log_{timestamp}.csv"
    log_file = open(log_filename, 'w')
    
    log_file.write(f"# Session started: {datetime.now().isoformat()}\n")
    log_file.write("timestamp,node_ip,node_name,sensor_value,is_master,master_duration_seconds,session_elapsed_seconds\n")
    log_file.flush()
    
    logging_active = True
    session_start_time = time.time()
    session_data.clear()
    all_masters_in_session = set()
    
    # Reset master durations
    for ip in ip_led_map:
        master_duration_data[ip] = 0.0
    
    # Clear unified graph data for web server
    graph_data_unified.clear()
    print("Graph data cleared for new session")
    
    # Clear LED matrix for new session
    if MATRIX_ENABLED and matrix_device:
        try:
            matrix_device.clear()
            print("LED Matrix cleared for new session")
        except:
            pass
    
    try:
        requests.post(WEB_SERVER_ENDPOINT, json={'button_action': 'start'}, timeout=1)
    except:
        pass
    
    print("\n" + "="*60)
    print("LOGGING SESSION STARTED")
    print(f"File: {log_filename}")
    print(f"Start: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60 + "\n")

def stop_logging_session():
    """Stop current logging session (button press #2)"""
    global logging_active, log_file
    
    if not logging_active:
        return
    
    session_duration = time.time() - session_start_time if session_start_time else 0
    
    if log_file and not log_file.closed:
        log_file.write(f"\n# Session ended: {datetime.now().isoformat()}\n")
        log_file.write(f"# Total session duration: {session_duration:.2f} seconds\n")
        log_file.write(f"\n# MASTER SUMMARY (Devices that became MASTER):\n")
        log_file.write("# IP Address,Name,Total Duration (seconds)\n")
        
        for ip in sorted(all_masters_in_session):
            name = ip_led_map[ip][0] if ip in ip_led_map else "UNKNOWN"
            duration = master_duration_data.get(ip, 0.0)
            log_file.write(f"# {ip},{name},{duration:.2f}\n")
        
        log_file.close()
    
    # Clear LED matrix
    if MATRIX_ENABLED and matrix_device:
        try:
            matrix_device.clear()
            print("LED Matrix cleared after session stop")
        except:
            pass
    
    try:
        requests.post(WEB_SERVER_ENDPOINT, json={'button_action': 'stop'}, timeout=1)
    except:
        pass
    
    print("\n" + "="*60)
    print("LOGGING SESSION STOPPED")
    print(f"File: {log_filename}")
    print(f"Duration: {session_duration:.2f} seconds")
    print(f"Masters: {len(all_masters_in_session)}")
    for ip in sorted(all_masters_in_session):
        name = ip_led_map[ip][0]
        duration = master_duration_data.get(ip, 0.0)
        print(f"   • {name} ({ip}): {duration:.2f}s")
    print("="*60 + "\n")
    
    logging_active = False

def log_data_point(ip, value, is_master):
    """Log a single data point during active session"""
    if not logging_active or not log_file or log_file.closed:
        return
    
    try:
        timestamp = datetime.now().isoformat()
        name = ip_led_map[ip][0] if ip in ip_led_map else "UNKNOWN"
        session_elapsed = time.time() - session_start_time if session_start_time else 0
        master_duration = master_duration_data.get(ip, 0.0)
        
        log_file.write(f"{timestamp},{ip},{name},{value},{is_master},{master_duration:.2f},{session_elapsed:.2f}\n")
        log_file.flush()
        
        if is_master:
            all_masters_in_session.add(ip)
            
    except Exception as e:
        print(f"Log error: {e}")

def button_pressed():
    """Handle button press - toggle logging on/off"""
    global logging_active
    
    try:
        yellow_led.on()
    except:
        pass
    
    if not logging_active:
        start_logging_session()
    else:
        stop_logging_session()
    
    def turn_off_yellow():
        try:
            yellow_led.off()
        except:
            pass
    
    threading.Timer(3.0, turn_off_yellow).start()

# ============================================================================
# DATA MANAGEMENT FUNCTIONS
# ============================================================================

def cleanup_old_data():
    """Remove data older than TIME_WINDOW seconds"""
    global graph_data_unified
    
    if graph_start_time is None:
        return
    
    current_time = time.time()
    cutoff_time = current_time - TIME_WINDOW
    
    while graph_data_unified and graph_data_unified[0]['timestamp'] < cutoff_time:
        graph_data_unified.popleft()

def update_master_duration():
    """Calculate how long each node has been master"""
    global master_duration_data
    
    if not logging_active or session_start_time is None:
        return
    
    current_time = time.time()
    cutoff_time = session_start_time
    
    for ip in ip_led_map:
        master_duration_data[ip] = 0.0
    
    last_master = None
    last_time = None
    
    for point in session_data:
        if point['timestamp'] >= cutoff_time:
            if point['is_master']:
                if last_master == point['ip'] and last_time is not None:
                    duration = point['timestamp'] - last_time
                    master_duration_data[point['ip']] += duration
                last_master = point['ip']
                last_time = point['timestamp']
    
    if last_master is not None and last_time is not None:
        duration = current_time - last_time
        master_duration_data[last_master] += duration

def calculate_matrix_graph():
    """Calculate LED pattern for MASTER DURATION BAR CHART"""
    current_time = time.time()
    cutoff_time = current_time - GRAPH_TIME_WINDOW
    device_row_data = {}
    
    ip_to_device_idx = {}
    for idx, ip in enumerate(sorted(ip_led_map.keys())):
        ip_to_device_idx[ip] = idx
    
    master_durations = {}
    for ip in ip_led_map:
        master_durations[ip] = 0.0
    
    last_master_ip = None
    last_time = None
    
    for point in graph_data_unified:
        if point['timestamp'] >= cutoff_time:
            if point['is_master']:
                if last_master_ip == point['ip'] and last_time is not None:
                    duration = point['timestamp'] - last_time
                    master_durations[point['ip']] += duration
                
                last_master_ip = point['ip']
                last_time = point['timestamp']
    
    if last_master_ip is not None and last_time is not None:
        duration = current_time - last_time
        master_durations[last_master_ip] += duration
    
    for ip in ip_led_map:
        device_idx = ip_to_device_idx[ip]
        row_values = [0] * NUM_ROWS
        
        duration_seconds = master_durations[ip]
        rows_to_light = int(duration_seconds / ROW_TIME)
        rows_to_light = min(rows_to_light, NUM_ROWS)
        
        for row in range(NUM_ROWS):
            if row >= (NUM_ROWS - rows_to_light):
                row_values[row] = 2
            else:
                row_values[row] = 0
        
        device_row_data[device_idx] = row_values
    
    return device_row_data

def get_log_files_list():
    """Get list of all log files in current directory"""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        log_files = []
        
        for filename in os.listdir(script_dir):
            if filename.startswith('swarm_log_') and filename.endswith('.csv'):
                filepath = os.path.join(script_dir, filename)
                try:
                    stat = os.stat(filepath)
                    log_files.append({
                        'name': filename,
                        'size': stat.st_size,
                        'modified': stat.st_mtime
                    })
                except:
                    pass
        
        log_files.sort(key=lambda x: x['modified'], reverse=True)
        return log_files
    except:
        return []

def send_to_web_server():
    """Send current state to web server"""
    global last_log_files_sent
    
    try:
        current_time = time.time()
        cutoff_time = current_time - TIME_WINDOW
        
        recent_data = [
            {
                'timestamp': point['timestamp'],
                'ip': point['ip'],
                'name': ip_led_map[point['ip']][0],
                'value': point['value'],
                'is_master': point['is_master']
            }
            for point in graph_data_unified
            if point['timestamp'] >= cutoff_time
        ]
        
        current_master = None
        for ip in ip_led_map:
            if node_is_master[ip]:
                current_master = {
                    'ip': ip,
                    'name': ip_led_map[ip][0],
                    'value': node_values[ip]
                }
                break
        
        script_dir = os.path.dirname(os.path.abspath(__file__))
        
        # Get current log files
        current_log_files = get_log_files_list()
        
        payload = {
            'timestamp': current_time,
            'nodes': {
                ip: {
                    'name': ip_led_map[ip][0],
                    'value': node_values[ip],
                    'is_master': node_is_master[ip],
                    'last_update': last_update_time[ip]
                }
                for ip in ip_led_map
            },
            'current_master': current_master,
            'graph_data': recent_data,
            'master_durations': {
                ip_led_map[ip][0]: master_duration_data[ip]
                for ip in ip_led_map
            },
            'logging_active': logging_active,
            'session_start': session_start_time,
            'log_directory': script_dir,
            'current_log_file': log_filename if logging_active else None,
            'log_files': current_log_files  # Send log files list
        }
        
        last_log_files_sent = current_log_files
        
        requests.post(WEB_SERVER_ENDPOINT, json=payload, timeout=1)
            
    except:
        pass


# ============================================================================
# THREADING FUNCTIONS
# ============================================================================

def web_server_update_loop():
    """Continuously send updates to web server"""
    print("✓ Web server update thread started")
    
    while not shutdown_flag.is_set():
        try:
            update_master_duration()
            send_to_web_server()
            time.sleep(0.5)
        except Exception as e:
            if not shutdown_flag.is_set():
                print(f"Web update error: {e}")
            time.sleep(1)

def matrix_update_thread():
    """Thread to update LED matrix with time-series graph"""
    if not MATRIX_ENABLED or not matrix_device:
        print("Matrix update thread not started (matrix disabled)")
        return
    
    print("LED Matrix Graph thread started")
    
    consecutive_errors = 0
    max_consecutive_errors = 5
    
    while not shutdown_flag.is_set():
        try:
            device_row_data = calculate_matrix_graph()
            matrix_device.display_row_graph(device_row_data)
            consecutive_errors = 0
            time.sleep(0.5)
            
        except Exception as e:
            consecutive_errors += 1
            
            if consecutive_errors <= 3:
                print(f"Matrix update error (attempt {consecutive_errors}): {e}")
            
            if consecutive_errors >= max_consecutive_errors:
                print(f"Matrix stopped after {max_consecutive_errors} consecutive errors")
                break
            
            time.sleep(1)
    
    print("Matrix update thread stopped")

def stop_all_leds():
    """Stop all LED blinking"""
    try:
        for led in led_objects.values():
            led.off()
        yellow_led.off()
    except:
        pass

def udp_listener():
    """Listen for UDP messages from ESP32 nodes"""
    global graph_start_time, session_start_time
    
    print(f"UDP listener started on port {UDP_PORT}")
    print(f"Listening for: {list(ip_led_map.keys())}")
    recv_sock.settimeout(1.0)
    
    while not shutdown_flag.is_set():
        try:
            data, addr = recv_sock.recvfrom(1024)
            msg = data.decode('utf-8').strip()
            sender_ip = addr[0]
            
            if sender_ip not in ip_led_map:
                continue
            
            current_time = time.time()
            
            if graph_start_time is None:
                graph_start_time = current_time
                print(f"\n{'='*60}")
                print("Data collection started")
                print(f"{'='*60}\n")
            
            if msg.startswith("MASTER:"):
                try:
                    value = int(msg.split(":")[1])
                    
                    node_values[sender_ip] = value
                    node_is_master[sender_ip] = True
                    last_update_time[sender_ip] = current_time
                    
                    for ip in ip_led_map:
                        if ip != sender_ip:
                            node_is_master[ip] = False
                    
                    try:
                        led_objects[sender_ip].on()
                        for ip in ip_led_map:
                            if ip != sender_ip:
                                led_objects[ip].off()
                    except:
                        pass
                    
                    graph_data_unified.append({
                        'timestamp': current_time,
                        'ip': sender_ip,
                        'value': value,
                        'is_master': True
                    })
                    
                    if logging_active:
                        session_data.append({
                            'timestamp': current_time,
                            'ip': sender_ip,
                            'value': value,
                            'is_master': True
                        })
                        log_data_point(sender_ip, value, True)
                    
                    node_name = ip_led_map[sender_ip][0]
                    status = "📝" if logging_active else "  "
                    print(f"{status} ★ [MASTER] {node_name:6s} ({sender_ip}): {value:4d}")
                    
                except (ValueError, IndexError) as e:
                    print(f"Parse error: {msg} - {e}")
            
            elif msg.startswith("SENSOR:") or msg.startswith("LIGHT:"):
                try:
                    value = int(msg.split(":")[1])
                    
                    node_values[sender_ip] = value
                    node_is_master[sender_ip] = False
                    last_update_time[sender_ip] = current_time
                    
                    graph_data_unified.append({
                        'timestamp': current_time,
                        'ip': sender_ip,
                        'value': value,
                        'is_master': False
                    })
                    
                    if logging_active:
                        session_data.append({
                            'timestamp': current_time,
                            'ip': sender_ip,
                            'value': value,
                            'is_master': False
                        })
                        log_data_point(sender_ip, value, False)
                    
                except (ValueError, IndexError) as e:
                    print(f"Parse error: {msg} - {e}")
        
        except socket.timeout:
            continue
        except Exception as e:
            if not shutdown_flag.is_set():
                print(f"UDP listener error: {e}")
            time.sleep(0.1)
    
    print("UDP listener stopped")

def cleanup_loop():
    """Periodically clean up old data"""
    while not shutdown_flag.is_set():
        try:
            cleanup_old_data()
            time.sleep(1)
        except Exception as e:
            if not shutdown_flag.is_set():
                print(f"Cleanup error: {e}")
            time.sleep(1)

# ============================================================================
# MAIN EXECUTION
# ============================================================================

def print_startup_banner():
    """Print startup information"""
    print("\n" + "="*60)
    print("  RASPBERRY PI LIGHT SWARM MONITOR")
    print("  BUTTON-BASED SESSION LOGGING + LED MATRIX")
    print("="*60)
    print(f"\n  Network Configuration:")
    print(f"   Local IP:     {LOCAL_IP}")
    print(f"   UDP Port:     {UDP_PORT}")
    print(f"   Broadcast:    {BROADCAST_IP}")
    print(f"\n Web Server:")
    print(f"   URL: {WEB_SERVER_URL}")
    print(f"\n Monitoring Nodes:")
    for ip, (name, pin) in ip_led_map.items():
        print(f"   {name:6s} - {ip:15s} (GPIO {pin})")
    print(f"\n Button Configuration:")
    print(f"   Button Pin:   GPIO {BUTTON_PIN}")
    print(f"   Yellow LED:   GPIO {YELLOW_LED_PIN}")
    print(f"\n Logging System:")
    print(f"   Press button once:  START logging (creates new file)")
    print(f"   Press button again: STOP logging (saves with summary)")
    print(f"\n LED Matrix:")
    matrix_status = "✓ Enabled" if MATRIX_ENABLED else "⚠ Disabled"
    print(f"   Status: {matrix_status}")
    if MATRIX_ENABLED:
        print(f"   Display: {NUM_ROWS} rows × {ROW_TIME:.2f}s = {GRAPH_TIME_WINDOW}s per device")
        print(f"   Shows: Master duration bar chart")
    print(f"\n Log File Contains:")
    print(f"   • All devices that became masters (IP addresses)")
    print(f"   • How long each device was master (from beginning)")
    print(f"   • Raw data from each master")
    print(f"   • Session summary at end of file")
    print("\n" + "="*60 + "\n")

def shutdown_handler():
    """Handle clean shutdown"""
    print("\n" + "="*60)
    print(" SHUTTING DOWN...")
    print("="*60)
    
    shutdown_flag.set()
    time.sleep(1)
    
    if logging_active:
        stop_logging_session()
    
    stop_all_leds()
    
    try:
        recv_sock.close()
        send_sock.close()
    except:
        pass
    
    cleanup_gpio()
    
    if MATRIX_ENABLED and matrix_device:
        try:
            matrix_device.clear()
            matrix_device.close()
            print("LED Matrix cleared")
        except:
            pass
    
    print("Cleanup complete")
    print("="*60 + "\n")

if __name__ == "__main__":
    try:
        # Initialize GPIO
        init_gpio()
        
        # Print startup information
        print_startup_banner()
        
        # Initialize LED Matrix
        if matrix_imports_available:
            init_led_matrix()
        else:
            print("LED Matrix initialization skipped (libraries not available)")
            print("   To install: pip3 install spidev\n")
        
        # Blink yellow LED to show ready
        try:
            yellow_led.on()
            time.sleep(0.3)
            yellow_led.off()
            time.sleep(0.2)
            yellow_led.on()
            time.sleep(0.3)
            yellow_led.off()
        except:
            pass
        
        # Start UDP listener thread
        udp_thread = threading.Thread(target=udp_listener, daemon=True)
        udp_thread.start()
        
        # Start web server update thread
        web_thread = threading.Thread(target=web_server_update_loop, daemon=True)
        web_thread.start()
        
        # Start data cleanup thread
        cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True)
        cleanup_thread.start()
        
        # Start LED Matrix update thread (if available)
        if MATRIX_ENABLED and matrix_device:
            matrix_thread = threading.Thread(target=matrix_update_thread, daemon=True)
            matrix_thread.start()
            print("LED Matrix Graph thread started")
        else:
            print("LED Matrix thread not started (matrix disabled)")
        
        time.sleep(0.5)
        
        print("\n" + "="*60)
        print("ALL SYSTEMS READY")
        print("="*60)
        print(f"\nPress BUTTON (GPIO {BUTTON_PIN}) to:")
        print("   1st press: START logging → creates new log file")
        print("   2nd press: STOP logging → saves file with summary")
        if MATRIX_ENABLED:
            print(f"\n LED Matrix: Master duration bar chart ({GRAPH_TIME_WINDOW}s)")
        print(f"\n Web dashboard: {WEB_SERVER_URL}")
        print("  Press Ctrl+C to stop")
        print("="*60 + "\n")
        
        # Main loop
        while True:
            time.sleep(1)
        
    except KeyboardInterrupt:
        print("\n\n Keyboard interrupt detected")
        
    except Exception as e:
        print(f"\n\n Fatal error: {e}")
        import traceback
        traceback.print_exc()
        
    finally:
        shutdown_handler()
