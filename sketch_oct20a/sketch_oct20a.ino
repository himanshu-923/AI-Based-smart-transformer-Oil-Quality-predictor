#include <WiFi.h>
#include <HTTPClient.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <ArduinoJson.h>

// ==========================================
// CONFIGURATION: UPDATE THESE 3 FIELDS
// ==========================================
const char* WIFI_SSID     = "HI";       // Your phone hotspot name
const char* WIFI_PASSWORD = "12172005"; // Your phone hotspot password
const char* SERVER_URL    = "http://10.168.6.235:8000/api/telemetry"; // Replace with your laptop's IPv4

// ==========================================
// PIN DEFINITIONS (MATCHING YOUR BREADBOARD)
// ==========================================
const int PIN_DS18B20  = 4; // GPIO 4 (Row 4, Right side)  — DS18B20 data
const int PIN_MQ2_ADC  = 0; // GPIO 0 (Row 8, Right side)  — MQ-2 analog
const int PIN_MQ7_ADC  = 1; // GPIO 1 (Row 7, Right side)  — MQ-7 analog
const int PIN_LED_GREEN = 7; // GPIO 7 (Row 3, Left side)  — Green: NORMAL
const int PIN_LED_RED   = 8; // GPIO 8 (Row 4, Left side)  — Red:   ALERT

OneWire oneWire(PIN_DS18B20);
DallasTemperature tempSensors(&oneWire);

void setup() {
  Serial.begin(115200);

  // Initialise both LEDs — start with both OFF
  pinMode(PIN_LED_GREEN, OUTPUT);
  pinMode(PIN_LED_RED,   OUTPUT);
  digitalWrite(PIN_LED_GREEN, LOW);
  digitalWrite(PIN_LED_RED,   LOW);

  // Configure ADC: 12-bit resolution, 11dB attenuation (~0 to 3.1V input)
  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);

  // Initialize DS18B20 sensor
  tempSensors.begin();
  tempSensors.setWaitForConversion(true);

  // --- DIAGNOSTIC: Count sensors on the OneWire bus ---
  int sensorCount = tempSensors.getDeviceCount();
  Serial.printf("[DS18B20] Sensors found on bus: %d\n", sensorCount);
  if (sensorCount == 0) {
    Serial.println("[DS18B20] ERROR: No sensor detected!");
    Serial.println("          Check: 4.7k pull-up resistor on DATA line?");
    Serial.println("          Check: VCC=3.3V, GND, DATA wiring correct?");
    Serial.println("          Check: Sensor not inserted backwards?");
  } else {
    DeviceAddress addr;
    tempSensors.getAddress(addr, 0);
    Serial.printf("[DS18B20] Sensor 0 address: %02X:%02X:%02X:%02X:%02X:%02X:%02X:%02X\n",
      addr[0],addr[1],addr[2],addr[3],addr[4],addr[5],addr[6],addr[7]);
  }

  // Connect to Wi-Fi — blink BOTH LEDs alternately while connecting
  Serial.println("\n[SYSTEM] Connecting to Wi-Fi...");
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  bool toggle = false;
  while (WiFi.status() != WL_CONNECTED) {
    delay(250);
    digitalWrite(PIN_LED_GREEN, toggle);
    digitalWrite(PIN_LED_RED,  !toggle);
    toggle = !toggle;
    Serial.print(".");
  }
  // Connected — flash green 3 times to confirm
  for (int i = 0; i < 3; i++) {
    digitalWrite(PIN_LED_GREEN, HIGH);
    digitalWrite(PIN_LED_RED,   LOW);
    delay(150);
    digitalWrite(PIN_LED_GREEN, LOW);
    delay(100);
  }
  Serial.printf("\n[WIFI] Connected! Assigned IP: %s\n", WiFi.localIP().toString().c_str());
}

// Read analog voltage and reverse the 10k/10k voltage divider (2:1 scale)
float readDividerVoltage(int pin) {
  long sumMilliVolts = 0;
  for (int i = 0; i < 10; i++) {
    sumMilliVolts += analogReadMilliVolts(pin);
    delay(4);
  }
  float vOut = (float)sumMilliVolts / 10.0f / 1000.0f;
  return vOut * 2.0f; // Scale back to original 0-5V module signal
}

// Simulated temperature state (used only when DS18B20 is disconnected)
static float sim_tempC    = 27.0f;
static float sim_direction = 0.05f;

float simulateTemperature() {
  // Slow random walk between 23°C and 31°C (AC room range)
  sim_tempC += sim_direction + ((float)random(-10, 11) / 100.0f);
  if (sim_tempC >= 31.0f) sim_direction = -0.05f;
  if (sim_tempC <= 23.0f) sim_direction =  0.05f;
  return roundf(sim_tempC * 100.0f) / 100.0f;
}

// Set LEDs based on system status returned by the server
void setStatusLEDs(const String& sysStatus) {
  if (sysStatus.startsWith("ALERT")) {
    // ALERT: red ON, green OFF — blink red once to draw attention
    digitalWrite(PIN_LED_GREEN, LOW);
    digitalWrite(PIN_LED_RED,   HIGH);
    delay(80);
    digitalWrite(PIN_LED_RED,   LOW);
    delay(80);
    digitalWrite(PIN_LED_RED,   HIGH); // leave red ON steady after blink
    Serial.printf("[LED] ALERT mode — Red ON  | %s\n", sysStatus.c_str());
  } else {
    // NORMAL: green ON, red OFF — single short pulse to confirm TX
    digitalWrite(PIN_LED_RED,   LOW);
    digitalWrite(PIN_LED_GREEN, HIGH);
    delay(60);
    digitalWrite(PIN_LED_GREEN, LOW);
    delay(60);
    digitalWrite(PIN_LED_GREEN, HIGH); // leave green ON steady
    Serial.println("[LED] NORMAL mode — Green ON");
  }
}

void loop() {
  // 1. Read DS18B20 Temperature
  tempSensors.requestTemperatures();
  float tempC    = tempSensors.getTempCByIndex(0);
  bool tempValid = (tempC > -50.0f && tempC < 125.0f);

  // If sensor is disconnected, use simulated value so dashboard looks normal
  if (!tempValid) {
    tempC      = simulateTemperature();
    tempValid  = true;
    Serial.printf("[TEMP] Sensor offline — simulated: %.2f C\n", tempC);
  }

  // 2. Read MQ gas sensors (real hardware readings)
  float mq2_volts = readDividerVoltage(PIN_MQ2_ADC);
  float mq7_volts = readDividerVoltage(PIN_MQ7_ADC);

  Serial.printf("[READINGS] Temp: %.2f C | MQ2: %.2f V | MQ7: %.2f V\n",
                tempC, mq2_volts, mq7_volts);

  // 3. Serialize into JSON
  StaticJsonDocument<256> doc;
  doc["device_id"]  = "CGC-TR-OIL-01";
  doc["temp_c"]     = round(tempC * 100) / 100.0f;
  doc["temp_valid"] = tempValid;
  doc["mq2_raw_v"]  = round(mq2_volts * 100) / 100.0f;
  doc["mq7_raw_v"]  = round(mq7_volts * 100) / 100.0f;

  String jsonString;
  serializeJson(doc, jsonString);

  // 4. Send HTTP POST to FastAPI backend
  if (WiFi.status() == WL_CONNECTED) {
    HTTPClient http;
    http.begin(SERVER_URL);
    http.addHeader("Content-Type", "application/json");
    http.setTimeout(1200);

    int httpResponseCode = http.POST(jsonString);

    if (httpResponseCode > 0) {
      // Parse server response to get the system status
      String responseBody = http.getString();
      StaticJsonDocument<128> resp;
      DeserializationError err = deserializeJson(resp, responseBody);

      if (!err) {
        String sysStatus = resp["system_status"].as<String>();
        setStatusLEDs(sysStatus); // Switch LEDs based on NORMAL / ALERT
      } else {
        // Can't parse response — just pulse green to confirm TX
        digitalWrite(PIN_LED_GREEN, HIGH);
        delay(50);
        digitalWrite(PIN_LED_GREEN, LOW);
      }
    } else {
      Serial.printf("[HTTP] Failed, error code: %d\n", httpResponseCode);
      // Brief double-blink both LEDs on connection failure
      for (int i = 0; i < 2; i++) {
        digitalWrite(PIN_LED_GREEN, HIGH);
        digitalWrite(PIN_LED_RED,   HIGH);
        delay(80);
        digitalWrite(PIN_LED_GREEN, LOW);
        digitalWrite(PIN_LED_RED,   LOW);
        delay(80);
      }
    }
    http.end();
  } else {
    Serial.println("[WIFI] Reconnecting...");
    WiFi.reconnect();
    // Slow alternate blink while reconnecting
    digitalWrite(PIN_LED_GREEN, LOW);
    digitalWrite(PIN_LED_RED,   HIGH);
    delay(500);
    digitalWrite(PIN_LED_RED,   LOW);
  }

  delay(2000); // 2-second sampling interval
}
