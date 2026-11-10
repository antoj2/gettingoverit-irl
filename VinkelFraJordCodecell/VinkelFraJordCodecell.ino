#include <CodeCell.h>

CodeCell myCodeCell;

void setup() {
  Serial.begin(115200);
  myCodeCell.Init(MOTION_GRAVITY);   
  Serial.print("JEG ER VÅGEN");
}

void loop() {
  if (myCodeCell.Run(10)) {          
    float gx, gy, gz;
    myCodeCell.Motion_GravityRead(gx, gy, gz); 

    float tilt = atan2(sqrt(gx * gx + gy * gy), gz);


    Serial.print("Tilt: ");
    Serial.print(tilt);
    Serial.print("\n");
  }
}