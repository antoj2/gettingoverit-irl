#include <CodeCell.h>
#include <WiFi.h>
#include <WiFiUdp.h>

CodeCell myCodeCell;
WiFiUDP udp;

const char *ssid = "SchimonsTuffESP32v2";
const char *password = "AntonAnton";
// Where the data is sent. Starts as the address the CodeCell's DHCP server normally gives the first device, and is replaced by the real address of whoever sends a packet (the "hello") to `port`. That keeps it working when the computer gets another address, e.g. after reconnecting with a random Wi-Fi MAC address, or when a phone joined first.
IPAddress computerIP(192, 168, 4, 2);
const int port = 4210;

int pickBestChannel() {
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  int n = WiFi.scanNetworks();

  int load[14] = {0};                       // index = channel 1..13
  for (int i = 0; i < n; i++) {
    int ch = WiFi.channel(i);
    int weight = 100 + WiFi.RSSI(i);        // stronger networks count more (RSSI is negative)
    if (weight < 1) weight = 1;
    // a 2.4 GHz channel overlaps its neighbours, so spread the load
    for (int c = ch - 2; c <= ch + 2; c++) {
      if (c >= 1 && c <= 13) load[c] += weight / (1 + abs(c - ch));
    }
  }
  WiFi.scanDelete();

  int best = 1;
  for (int c : {1, 6, 11}) {                // non-overlapping channels only
    if (load[c] < load[best]) best = c;
  }
  return best;
}

void setup() {
  myCodeCell.Init(MOTION_GRAVITY);
  int channel = pickBestChannel();
  WiFi.mode(WIFI_AP);
  WiFi.softAP(ssid, password, channel);
  WiFi.setSleep(false);
  udp.begin(port);  // listen for the computer's hello
}

uint32_t lastBatteryCheck = 0;
uint8_t batteryLevel = 100;
uint32_t nrpackage = 1;

void loop() {
  // Any packet to `port` is a hello: from now on send to where it came from (unicast, so no broadcast latency).
  while (udp.parsePacket() > 0) {
    computerIP = udp.remoteIP();
  }

  if (myCodeCell.Run(50)) {
    if (millis() - lastBatteryCheck > 30000) {
      batteryLevel = myCodeCell.BatteryLevelRead();
      lastBatteryCheck = millis();
    }
    float gx, gy, gz;
    myCodeCell.Motion_GravityRead(gx, gy, gz); 

    float tilt = atan2(sqrt(gx * gx + gy * gy), gz);


    // send over wifi
    udp.beginPacket(computerIP, port);

    udp.print(nrpackage);
    udp.print(",");
    udp.print(millis());
    udp.print(",");
    udp.print(tilt);
    udp.print(",");
    udp.print(batteryLevel);
    udp.endPacket();
    ++nrpackage;
  }
}