#include <Arduino.h>
#include <BleGamepad.h>
#include <CodeCell.h>

CodeCell myCodeCell;

float Roll = 0.0;
float Pitch = 0.0;
float Yaw = 0.0;


//BleGamepad bleGamepad;
BleGamepad bleGamepad("skatepad", "orksat.me", 100); // Set custom device name, manufacturer and initial battery level
BleGamepadConfiguration bleGamepadConfig;                          // Create a BleGamepadConfiguration object to store all of the options



const int delayBetweenHIDReports = 5; // Additional delay in milliseconds between HID reports

void setup()
{
    Serial.begin(115200);
    myCodeCell.Init(MOTION_ROTATION_NO_MAG); //Initializes Rotation Sensing
    Serial.println("Starting BLE work!");
    //bleGamepad.begin();
    bleGamepadConfig.setAutoReport(true);
    bleGamepadConfig.setControllerType(CONTROLLER_TYPE_GAMEPAD); // CONTROLLER_TYPE_JOYSTICK, CONTROLLER_TYPE_GAMEPAD (DEFAULT), CONTROLLER_TYPE_MULTI_AXIS
    bleGamepadConfig.setButtonCount(1);
    bleGamepadConfig.setHatSwitchCount(0);
    bleGamepadConfig.setWhichSpecialButtons(false, false, false, false, false, false, false, false);
    bleGamepadConfig.setWhichAxes(true, true, true, false, false, false, false, false);
    bleGamepadConfig.setIncludeThrottle(false);

    bleGamepadConfig.setVid(0xdead);
    bleGamepadConfig.setPid(0xbeef);
    
    bleGamepadConfig.setModelNumber("1.0");
    bleGamepadConfig.setSoftwareRevision("Software Rev 1");
    bleGamepadConfig.setSerialNumber("24404972");
    bleGamepadConfig.setFirmwareRevision("2.0");
    bleGamepadConfig.setHardwareRevision("0.1");
    
    // Some non-Windows operating systems and web based gamepad testers don't like min axis set below 0, so 0 is set by default
    //bleGamepadConfig.setAxesMin(0x8001); // -32767 --> int16_t - 16 bit signed integer - Can be in decimal or hexadecimal
  	bleGamepadConfig.setAxesMin(0x0000); // 0 --> int16_t - 16 bit signed integer - Can be in decimal or hexadecimal    
    bleGamepadConfig.setAxesMax(0x7FFF); // 32767 --> int16_t - 16 bit signed integer - Can be in decimal or hexadecimal     
    
    bleGamepad.begin(&bleGamepadConfig); // Begin gamepad with configuration options
    
}

int16_t adjustedPitch=0;
int16_t adjustedRoll=0;
int16_t adjustedYaw=0;

unsigned long lastTimeBatteryWasChecked=0;
void loop()
{

if(millis()-lastTimeBatteryWasChecked>10000){

  //Do some quick and dirty battery status stuff every 10 seconds:
       
        uint16_t lvl = myCodeCell.BatteryLevelRead();
        if (lvl <= 100) {     
                        bleGamepad.setBatteryLevel(lvl);
                        bleGamepad.setDischargingState(POWER_STATE_DISCHARGING);
                        if(lvl<30) bleGamepad.setPowerLevel(POWER_STATE_CRITICAL); 
                        else bleGamepad.setPowerLevel(POWER_STATE_GOOD); 
                        } 
        else if (lvl == 101) { bleGamepad.setChargingState(POWER_STATE_CHARGING); }  //charging
        else if (lvl == 102) { bleGamepad.setBatteryPowerInformation(POWER_STATE_NOT_PRESENT); } //USB power
        
        
        Serial.print("Battery: ");
        Serial.print(lvl);
        Serial.print("%, ");



  lastTimeBatteryWasChecked=millis();
}

if (bleGamepad.isConnected())
    {
        if (myCodeCell.Run(10)) {  //Run every 10Hz
        myCodeCell.Motion_RotationNoMagRead(Roll, Pitch, Yaw);
        
        
        Serial.printf("Roll: %.2f°, Pitch: %.2f°, Yaw: %.2f°\n", Roll, Pitch, Yaw);
        
        int pitchMinimum = -30;
        int pitchMaximum = 30;
        int rollMinimum = -32;
        int rollMaximum = 28;
            
        adjustedPitch = constrain(Pitch, pitchMinimum, pitchMaximum);
        adjustedRoll = constrain(Roll, rollMinimum, rollMaximum);
        adjustedYaw = constrain(Yaw, 0, 359);


        // Map adjusted pitch and roll to 32737 ~ 0 for use as an axis reading
        adjustedPitch = map(adjustedPitch, pitchMinimum, pitchMaximum, 32737, 0);
        adjustedRoll = map(adjustedRoll, rollMinimum, rollMaximum, 32737, 0);
        adjustedYaw = map(adjustedYaw, 0, 359, 32737, 0);

        


        }
        
        // Update throttle axis and auto-send report
        bleGamepad.setAxes(adjustedPitch,adjustedRoll,adjustedYaw);

        //Hvis man stejler på sit skateboard:
        if(Pitch>20 and Pitch<180) bleGamepad.press(BUTTON_1);
        else bleGamepad.release(BUTTON_1);
        
        delay(delayBetweenHIDReports);


   }
}