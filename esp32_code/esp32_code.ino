#include <WiFi.h>
#include <WiFiUdp.h>

// WiFi credentials
const char* ssid = "deep_laptop";
const char* password = "anshu.com";

// Pins
#define LED_PIN 4       // LED
#define ONBOARD_LED 2   // Onboard LED for Master
#define PHOTO_PIN 34    // Analog light sensor

// LED Bar Graph pins (10 LEDs)
#define BAR_LED_1  13
#define BAR_LED_2  12
#define BAR_LED_3  14
#define BAR_LED_4  27
#define BAR_LED_5  26
#define BAR_LED_6  25
#define BAR_LED_7  33
#define BAR_LED_8  32
#define BAR_LED_9  15
#define BAR_LED_10 5

const int barLedPins[10] = {BAR_LED_1, BAR_LED_2, BAR_LED_3, BAR_LED_4, BAR_LED_5,
                             BAR_LED_6, BAR_LED_7, BAR_LED_8, BAR_LED_9, BAR_LED_10};

WiFiUDP udp;
WiFiUDP udpESPOnly;
unsigned int localPort = 4210;
unsigned int espOnlyPort = 4211;  // Different port for ESP coordination
IPAddress broadcastIP;

struct DeviceInfo {
  IPAddress ip;
  int lightValue;
  unsigned long lastSeen;
};

DeviceInfo devices[10];
int deviceCount = 0;

int myLightValue = 0;
IPAddress masterIP;

#define SILENT_THRESHOLD 100   
#define MASTER_TIMEOUT 3000    
#define DEVICE_TIMEOUT 5000    
unsigned long lastPacketReceived = 0;
unsigned long lastBroadcastTime = 0;
unsigned long lastMasterSeen = 0;

bool isMaster = false;

const int pwmFreq = 5000;     
const int pwmResolution = 8; 

void setup() {
  Serial.begin(115200);
  
  ledcAttach(LED_PIN, pwmFreq, pwmResolution);
  ledcWrite(LED_PIN, 0);

  pinMode(ONBOARD_LED, OUTPUT);
  digitalWrite(ONBOARD_LED, LOW);

  pinMode(PHOTO_PIN, INPUT);

  // Initialize LED bar graph pins (safe even if not connected)
  for (int i = 0; i < 10; i++) {
    pinMode(barLedPins[i], OUTPUT);
    digitalWrite(barLedPins[i], LOW);
  }

  Serial.println("\n--- ESP32 Light Sensor Node with LED Bar Graph ---");

  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);
  Serial.print("Connecting to WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(300);
    Serial.print(".");
  }
  Serial.println("\nWiFi connected");
  Serial.print("Local IP: "); Serial.println(WiFi.localIP());

  broadcastIP = ~WiFi.subnetMask() | WiFi.localIP();
  Serial.print("Broadcast IP: "); Serial.println(broadcastIP);

  udp.begin(localPort);
  udpESPOnly.begin(espOnlyPort);
  Serial.printf("Listening UDP on port %d (RPi) and %d (ESP coordination)\n", localPort, espOnlyPort);
  
  lastMasterSeen = millis();
}

void updateDevice(IPAddress ip, int lightValue) {
  unsigned long now = millis();
  
  for (int i = 0; i < deviceCount; i++) {
    if (devices[i].ip == ip) {
      devices[i].lightValue = lightValue;
      devices[i].lastSeen = now;
      return;
    }
  }
  
  if (deviceCount < 10) {
    devices[deviceCount].ip = ip;
    devices[deviceCount].lightValue = lightValue;
    devices[deviceCount].lastSeen = now;
    deviceCount++;
  }
}

void cleanupDevices() {
  unsigned long now = millis();
  
  for (int i = 0; i < deviceCount; i++) {
    if (now - devices[i].lastSeen > DEVICE_TIMEOUT) {
      for (int j = i; j < deviceCount - 1; j++) {
        devices[j] = devices[j + 1];
      }
      deviceCount--;
      i--;
      Serial.println("Device timed out - removed from list");
    }
  }
}

void electMaster() {
  int highestLight = myLightValue;
  IPAddress newMaster = WiFi.localIP();

  for (int i = 0; i < deviceCount; i++) {
    if (devices[i].lightValue > highestLight) {
      highestLight = devices[i].lightValue;
      newMaster = devices[i].ip;
    } else if (devices[i].lightValue == highestLight) {
      if (devices[i].ip < newMaster) {
        newMaster = devices[i].ip;
      }
    }
  }
  
  if (newMaster != masterIP) {
    masterIP = newMaster;
    Serial.println("\n=== NEW MASTER ELECTED ===");
    Serial.printf("Master IP: %s (Light: %d)\n", masterIP.toString().c_str(), highestLight);
  }

  bool wasMaster = isMaster;
  isMaster = (masterIP == WiFi.localIP());
  
  if (isMaster != wasMaster) {
    Serial.printf("I am now %s\n\n", isMaster ? "MASTER" : "NOT MASTER");
  }

  digitalWrite(ONBOARD_LED, isMaster ? HIGH : LOW);
}

void updateLEDBarGraph(int lightValue) {
  // Map light value (0-4095) to number of LEDs to light up (0-10)
  int numLedsOn = map(lightValue, 0, 4095, 0, 10);
  
  // Constrain to valid range
  numLedsOn = constrain(numLedsOn, 0, 10);
  
  // Update each LED in the bar graph
  for (int i = 0; i < 10; i++) {
    if (i < numLedsOn) {
      digitalWrite(barLedPins[i], HIGH);
    } else {
      digitalWrite(barLedPins[i], LOW);
    }
  }
}

void loop() {
  unsigned long now = millis();

  myLightValue = analogRead(PHOTO_PIN);
  
  // Update PWM LED
  int brightness = map(myLightValue, 0, 4095, 0, 255);
  ledcWrite(LED_PIN, brightness);
  
  // Update LED bar graph (safe even if not physically connected)
  updateLEDBarGraph(myLightValue);
  
  Serial.printf("Light: %d, Brightness: %d | Master: %s (%s)\n", 
                myLightValue, brightness, 
                isMaster ? "YES" : "NO",
                masterIP.toString().c_str());

  int packetSize = udp.parsePacket();
  if (packetSize == 0) {
    packetSize = udpESPOnly.parsePacket();
  }
  
  WiFiUDP* activeUDP = (udp.parsePacket() > 0) ? &udp : &udpESPOnly;
  
  while (packetSize) {
    char packetBuffer[64];
    int len = activeUDP->read(packetBuffer, sizeof(packetBuffer) - 1);
    packetBuffer[len] = 0;
    
    IPAddress senderIP = activeUDP->remoteIP();
    
    if (strncmp(packetBuffer, "RESET", 5) == 0) {
      Serial.println("\n*** RESET RECEIVED FROM RASPBERRY PI ***");
      
      deviceCount = 0;
      masterIP = IPAddress(0, 0, 0, 0);
      isMaster = false;
      digitalWrite(ONBOARD_LED, LOW);
      
      // Turn off all bar graph LEDs
      for (int i = 0; i < 10; i++) {
        digitalWrite(barLedPins[i], LOW);
      }
      
      lastMasterSeen = now;
      lastPacketReceived = now;
      lastBroadcastTime = now;
      
      Serial.println("System reset complete\n");
    }
    else if (strncmp(packetBuffer, "LIGHT:", 6) == 0) {
      int receivedLight = atoi(packetBuffer + 6);
      
      Serial.printf("Received from %s: %d\n", senderIP.toString().c_str(), receivedLight);
      
      updateDevice(senderIP, receivedLight);
      
      if (senderIP == masterIP) {
        lastMasterSeen = now;
      }
    }
    else if (strncmp(packetBuffer, "MASTER:", 7) == 0) {
      int receivedLight = atoi(packetBuffer + 7);
      
      Serial.printf("MASTER message from %s: %d\n", senderIP.toString().c_str(), receivedLight);
      
      updateDevice(senderIP, receivedLight);
      
      if (senderIP == masterIP) {
        lastMasterSeen = now;
      }
    }
    
    lastPacketReceived = now;
    
    packetSize = udp.parsePacket();
    if (packetSize == 0) {
      packetSize = udpESPOnly.parsePacket();
      activeUDP = &udpESPOnly;
    } else {
      activeUDP = &udp;
    }
  }

  cleanupDevices();
  
  if (now - lastMasterSeen > MASTER_TIMEOUT && masterIP != WiFi.localIP()) {
    Serial.println("Master timeout detected - re-electing...");
    lastMasterSeen = now; 
  }
  
  electMaster();

  if (now - lastPacketReceived > SILENT_THRESHOLD && 
      now - lastBroadcastTime > SILENT_THRESHOLD) {
    
    if (isMaster) {
      String msg = "MASTER:" + String(myLightValue);
      udp.beginPacket(broadcastIP, localPort);
      udp.print(msg);
      udp.endPacket();
      Serial.println(">>> Sending MASTER message to RPi (port 4210) <<<");
      
      udpESPOnly.beginPacket(broadcastIP, espOnlyPort);
      udpESPOnly.print(msg);
      udpESPOnly.endPacket();
    } else {
      String msg = "LIGHT:" + String(myLightValue);
      udpESPOnly.beginPacket(broadcastIP, espOnlyPort);
      udpESPOnly.print(msg);
      udpESPOnly.endPacket();
    }
    
    lastBroadcastTime = now;
  }

  delay(50);
}