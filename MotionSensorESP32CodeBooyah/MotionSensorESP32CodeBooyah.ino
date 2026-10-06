#include <Arduino.h>
#include <CodeCell.h>

CodeCell myCodeCell;

float x = 0.0;
float y = 0.0;
float z = 0.0;



constexpr int delayBetweenHIDReports = 5; // Additional delay in milliseconds between HID reports

void setup()
{
    Serial.begin(115200);
    myCodeCell.Init(MOTION_ACCELEROMETER); // starter bevægelses ting

    
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
    
    }

    //Hvis man stejler på sit skateboard:
    delay(delayBetweenHIDReports);
}