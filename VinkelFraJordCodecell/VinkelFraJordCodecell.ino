#include <CodeCell.h>
#include <WiFi.h>
#include <WiFiUdp.h>

CodeCell myCodeCell;
WiFiUDP udp;

const char *ssid = "SchimonsTuffESP32v2";
const char *password = "AntonAnton";
const IPAddress computerIP(192, 168, 4, 2);
const int port = 4210;



void setup() {
  myCodeCell.Init(MOTION_GRAVITY);   
  WiFi.mode(WIFI_AP);
  WiFi.softAP(ssid, password);
}

uint32_t lastBatteryCheck = 0;
uint8_t batteryLevel = 100;

void loop() {
  if (myCodeCell.Run(10)) {
    if (millis() - lastBatteryCheck > 30000) {
      batteryLevel = myCodeCell.BatteryLevelRead();
      lastBatteryCheck = millis();
    }
    float gx, gy, gz;
    myCodeCell.Motion_GravityRead(gx, gy, gz); 

    float tilt = atan2(sqrt(gx * gx + gy * gy), gz);


    // send over wifi
    if (WiFi.softAPgetStationNum() > 0) {
      udp.beginPacket(computerIP, port);
      udp.print(tilt);
      udp.print(",");
      udp.print(batteryLevel);
      udp.endPacket();
    }
  }
}