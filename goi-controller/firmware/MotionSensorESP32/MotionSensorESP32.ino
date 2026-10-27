// ============================================================
//  Getting Over It stick controller - CodeCell firmware
//
//  Streams the sensor's absolute ORIENTATION (quaternion) over UDP.
//  No integration happens here any more, so there is no drift:
//  the PC (goi_bridge) turns the orientation into the hammer angle.
//
//  Packet (little-endian, 40 bytes) - must match src/goi_bridge/packets.py
//     uint16 magic 0x4F47 | uint8 version | uint8 battery% | uint32 seq | uint32 t_ms
//     float qw qx qy qz   | float ax ay az (linear accel, m/s^2, sensor frame)
// ============================================================
#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <CodeCell.h>

CodeCell myCodeCell;

// ---------------- Wi-Fi ----------------
const char* ssid     = "SchimonsTuffESP32";
const char* password = "VerityErTuff";
const uint16_t PORT  = 4210;

// 1 = broadcast to the access point's subnet (the PC may have any IP).
// 0 = unicast to PC_IP below (use this if broadcast does not arrive).
#define USE_BROADCAST 1
const IPAddress BROADCAST_IP(192, 168, 4, 255);
const IPAddress PC_IP(192, 168, 4, 2);

// ---------------- Rate ----------------
const uint8_t SEND_HZ = 100;

WiFiUDP udp;

struct __attribute__((packed)) ImuPacket {
  uint16_t magic;
  uint8_t  version;
  uint8_t  battery;
  uint32_t seq;
  uint32_t t_ms;
  float    q[4];     // w, x, y, z  (sensor -> world, world Z = up)
  float    acc[3];   // linear acceleration (gravity removed)
};
static_assert(sizeof(ImuPacket) == 40, "packet layout changed - update packets.py too");

uint32_t seq = 0;
uint8_t  batteryLevel = 100;
unsigned long lastBatteryCheck = 0;

void setup() {
  // Rotation vector = orientation. Linear acceleration is sent along for future use.
  // If the heading (yaw) wanders because of magnetic interference, try swapping
  // MOTION_ROTATION for MOTION_ROTATION_NO_MAG (if your CodeCell library has it);
  // the PC re-measures "screen right" every session either way.
  myCodeCell.Init(MOTION_LINEAR_ACC + MOTION_ROTATION);

  WiFi.mode(WIFI_AP);
  WiFi.softAP(ssid, password);
  WiFi.setSleep(false);           // no power-save latency spikes
}

void loop() {
  if (millis() - lastBatteryCheck > 30000 || lastBatteryCheck == 0) {
    uint16_t b = myCodeCell.BatteryLevelRead();
    batteryLevel = b > 100 ? 100 : (uint8_t)b;
    lastBatteryCheck = millis();
  }

  if (myCodeCell.Run(SEND_HZ)) {
    float qR, qI, qJ, qK;
    float ax = 0, ay = 0, az = 0;
    myCodeCell.Motion_RotationVectorRead(qR, qI, qJ, qK);
    myCodeCell.Motion_LinearAccRead(ax, ay, az);

    float len = sqrtf(qR * qR + qI * qI + qJ * qJ + qK * qK);
    if (len < 0.0001f) return;    // sensor not ready yet
    qR /= len; qI /= len; qJ /= len; qK /= len;

    ImuPacket p;
    p.magic   = 0x4F47;
    p.version = 1;
    p.battery = batteryLevel;
    p.seq     = seq++;
    p.t_ms    = millis();
    p.q[0] = qR; p.q[1] = qI; p.q[2] = qJ; p.q[3] = qK;
    p.acc[0] = ax; p.acc[1] = ay; p.acc[2] = az;

#if USE_BROADCAST
    udp.beginPacket(BROADCAST_IP, PORT);
#else
    udp.beginPacket(PC_IP, PORT);
#endif
    udp.write((const uint8_t*)&p, sizeof(p));
    udp.endPacket();
  }
}
