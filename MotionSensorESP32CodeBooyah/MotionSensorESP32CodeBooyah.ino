#include <Arduino.h>
#include <CodeCell.h>

CodeCell myCodeCell;

float x, y, z, dx, dy, dz {0.0};

constexpr int delayBetweenHIDReports = 5; // Additional delay in milliseconds between HID reports

void setup()
{
    Serial.begin(115200);
    myCodeCell.Init(MOTION_LINEAR_ACC); // starter bevægelses ting  
    Serial.print("STARTET");
}


unsigned long lastTimeBatteryWasChecked=0;
void loop()
{
  if(millis()-lastTimeBatteryWasChecked>30000)
  { // batteri balade
    uint16_t lvl = myCodeCell.BatteryLevelRead();
    Serial.print("Battery: ");
    Serial.print(lvl);
    Serial.print("%, ");
    lastTimeBatteryWasChecked=millis();
  }
  if (myCodeCell.Run(10)) 
  {  //Run every 10Hz
    myCodeCell.Motion_LinearAccRead(dx, dy, dz);
  } 
  if ((dx < 0.05) && (dx > -0.05)) {dx=0;}
  if ((dy < 0.05) && (dy > -0.05)) {dy=0;}
  if ((dz < 0.05) && (dz > -0.05)) {dz=0;}
  x += dx;
  y += dy;
  z += dz;
  Serial.printf("x = %.2f, y = %.2f, z = %.2f\r\n", dx, dy, dz);
  delay(delayBetweenHIDReports);
}