#include <Arduino.h>
#include <CodeCell.h>

CodeCell myCodeCell;


// ============================================================
// POSITION
// ============================================================

float posX = 0.0f;
float posY = 0.0f;


// ============================================================
// VELOCITY
// ============================================================

float velX = 0.0f;
float velY = 0.0f;


// ============================================================
// ACCELERATION
// ============================================================

float ax, ay, az;

float worldAx = 0.0f;
float worldAy = 0.0f;


// Previous acceleration
float previousAx = 0.0f;
float previousAy = 0.0f;


// ============================================================
// LOW PASS FILTER
// ============================================================

float filteredAx = 0.0f;
float filteredAy = 0.0f;

const float FILTER_ALPHA = 0.25f;


// ============================================================
// ACCELERATION BIAS
// ============================================================
//
// This is learned while the CodeCell is stationary.
//
// It compensates for a constant accelerometer offset.
//

float biasX = 0.0f;
float biasY = 0.0f;

const float BIAS_LEARNING_RATE = 0.01f;


// ============================================================
// QUATERNION
// ============================================================

float qR, qI, qJ, qK;


// Initial orientation
float q0R, q0I, q0J, q0K;

bool referenceSet = false;


// ============================================================
// TIMING
// ============================================================

unsigned long lastUpdate = 0;
unsigned long lastBatteryCheck = 0;


// ============================================================
// DRIFT / MOTION SETTINGS
// ============================================================

const float ACCELERATION_DEADBAND = 0.10f;

const float STATIONARY_ACCELERATION = 0.12f;

const float STATIONARY_VELOCITY = 0.05f;


// Number of consecutive stationary samples required before
// applying zero velocity correction.
const int STATIONARY_SAMPLES_REQUIRED = 15;

int stationarySamples = 0;


// ============================================================
// QUATERNION MULTIPLICATION
// ============================================================

void quaternionMultiply(
    float ar, float ai, float aj, float ak,
    float br, float bi, float bj, float bk,
    float &r, float &i, float &j, float &k)
{
    r = ar * br
      - ai * bi
      - aj * bj
      - ak * bk;

    i = ar * bi
      + ai * br
      + aj * bk
      - ak * bj;

    j = ar * bj
      - ai * bk
      + aj * br
      + ak * bi;

    k = ar * bk
      + ai * bj
      - aj * bi
      + ak * br;
}


// ============================================================
// ROTATE VECTOR
// ============================================================

void rotateVector(
    float qr,
    float qi,
    float qj,
    float qk,

    float vx,
    float vy,
    float vz,

    float &outX,
    float &outY,
    float &outZ)
{
    float r1, i1, j1, k1;

    quaternionMultiply(
        qr, qi, qj, qk,
        0.0f, vx, vy, vz,
        r1, i1, j1, k1
    );


    float r2, i2, j2, k2;

    quaternionMultiply(
        r1, i1, j1, k1,
        qr, -qi, -qj, -qk,
        r2, i2, j2, k2
    );


    outX = i2;
    outY = j2;
    outZ = k2;
}


// ============================================================
// SETUP
// ============================================================

void setup()
{
    Serial.begin(115200);


    myCodeCell.Init(
        MOTION_LINEAR_ACC +
        MOTION_ROTATION
    );


    Serial.println();
    Serial.println("=================================");
    Serial.println("CodeCell 2D Vertical Tracker");
    Serial.println("=================================");
    Serial.println();

    Serial.println("Hold CodeCell STILL.");
    Serial.println("X+ = person's RIGHT");
    Serial.println("X- = person's LEFT");
    Serial.println("Y+ = UP");
    Serial.println("Y- = DOWN");
    Serial.println();

    delay(1500);

    lastUpdate = micros();
}


// ============================================================
// LOOP
// ============================================================

void loop()
{
    // --------------------------------------------------------
    // Battery
    // --------------------------------------------------------

    if (millis() - lastBatteryCheck > 30000)
    {
        uint16_t level =
            myCodeCell.BatteryLevelRead();

        Serial.print("Battery: ");
        Serial.print(level);
        Serial.println("%");

        lastBatteryCheck = millis();
    }


    // --------------------------------------------------------
    // IMU update
    // --------------------------------------------------------

    if (myCodeCell.Run(100))
    {
        // ----------------------------------------------------
        // TIME
        // ----------------------------------------------------

        unsigned long now = micros();

        float dt =
            (now - lastUpdate) / 1000000.0f;

        lastUpdate = now;


        if (dt <= 0.0f || dt > 0.1f)
            return;


        // ----------------------------------------------------
        // READ LINEAR ACCELERATION
        // ----------------------------------------------------

        myCodeCell.Motion_LinearAccRead(
            ax,
            ay,
            az
        );


        // ----------------------------------------------------
        // READ ORIENTATION
        // ----------------------------------------------------

        myCodeCell.Motion_RotationVectorRead(
            qR,
            qI,
            qJ,
            qK
        );


        // ----------------------------------------------------
        // NORMALIZE QUATERNION
        // ----------------------------------------------------

        float qLength =
            sqrt(
                qR * qR +
                qI * qI +
                qJ * qJ +
                qK * qK
            );


        if (qLength < 0.0001f)
            return;


        qR /= qLength;
        qI /= qLength;
        qJ /= qLength;
        qK /= qLength;


        // ----------------------------------------------------
        // INITIAL REFERENCE
        // ----------------------------------------------------

        if (!referenceSet)
        {
            q0R = qR;
            q0I = qI;
            q0J = qJ;
            q0K = qK;

            referenceSet = true;

            Serial.println("REFERENCE SET");
            Serial.println();

            return;
        }


        // ----------------------------------------------------
        // CURRENT ROTATION RELATIVE TO START
        // ----------------------------------------------------

        float relR;
        float relI;
        float relJ;
        float relK;


        quaternionMultiply(
            q0R,
            -q0I,
            -q0J,
            -q0K,

            qR,
            qI,
            qJ,
            qK,

            relR,
            relI,
            relJ,
            relK
        );


        // ----------------------------------------------------
        // TRANSFORM ACCELERATION
        // ----------------------------------------------------

        float transformedX;
        float transformedY;
        float transformedZ;


        rotateVector(
            relR,
            relI,
            relJ,
            relK,

            ax,
            ay,
            az,

            transformedX,
            transformedY,
            transformedZ
        );


        worldAx = transformedX;
        worldAy = transformedY;


        // ====================================================
        // LOW PASS FILTER
        // ====================================================

        filteredAx =
            FILTER_ALPHA * worldAx +
            (1.0f - FILTER_ALPHA) * filteredAx;

        filteredAy =
            FILTER_ALPHA * worldAy +
            (1.0f - FILTER_ALPHA) * filteredAy;


        // ====================================================
        // DETECT STATIONARY STATE
        // ====================================================

        bool stationary =
            fabs(filteredAx) < STATIONARY_ACCELERATION &&
            fabs(filteredAy) < STATIONARY_ACCELERATION;


        if (stationary)
        {
            stationarySamples++;

            if (stationarySamples >= STATIONARY_SAMPLES_REQUIRED)
            {
                // ------------------------------------------------
                // ZERO VELOCITY UPDATE
                //
                // If the CodeCell has been still for long enough,
                // its real velocity must be zero.
                // ------------------------------------------------

                velX = 0.0f;
                velY = 0.0f;


                // ------------------------------------------------
                // LEARN ACCELEROMETER BIAS
                // ------------------------------------------------

                biasX =
                    biasX * (1.0f - BIAS_LEARNING_RATE)
                    + filteredAx * BIAS_LEARNING_RATE;

                biasY =
                    biasY * (1.0f - BIAS_LEARNING_RATE)
                    + filteredAy * BIAS_LEARNING_RATE;
            }
        }
        else
        {
            stationarySamples = 0;
        }


        // ====================================================
        // REMOVE BIAS
        // ====================================================

        float correctedAx =
            filteredAx - biasX;

        float correctedAy =
            filteredAy - biasY;


        // ====================================================
        // ACCELERATION DEAD BAND
        // ====================================================

        if (fabs(correctedAx) < ACCELERATION_DEADBAND)
            correctedAx = 0.0f;

        if (fabs(correctedAy) < ACCELERATION_DEADBAND)
            correctedAy = 0.0f;


        // ====================================================
        // TRAPEZOIDAL INTEGRATION
        //
        // Better than:
        //
        // velocity += acceleration * dt
        //
        // because it uses the average acceleration between
        // samples.
        // ====================================================

        float newVelX =
            velX +
            0.5f * (previousAx + correctedAx) * dt;

        float newVelY =
            velY +
            0.5f * (previousAy + correctedAy) * dt;


        // ====================================================
        // POSITION INTEGRATION
        //
        // Use average velocity.
        // ====================================================

        posX +=
            0.5f * (velX + newVelX) * dt;

        posY +=
            0.5f * (velY + newVelY) * dt;


        velX = newVelX;
        velY = newVelY;


        // ====================================================
        // SAVE ACCELERATION
        // ====================================================

        previousAx = correctedAx;
        previousAy = correctedAy;


        // ====================================================
        // ADDITIONAL VELOCITY CLAMP
        //
        // Only apply this when acceleration is essentially zero.
        // This prevents tiny numerical errors from keeping the
        // CodeCell moving forever.
        // ====================================================

        if (fabs(correctedAx) < 0.02f &&
            fabs(correctedAy) < 0.02f)
        {
            if (fabs(velX) < STATIONARY_VELOCITY)
                velX = 0.0f;

            if (fabs(velY) < STATIONARY_VELOCITY)
                velY = 0.0f;
        }


        // ====================================================
        // OUTPUT
        // ====================================================

        Serial.printf(
            "POS X=%+.3f Y=%+.3f | "
            "VEL X=%+.3f Y=%+.3f | "
            "ACC X=%+.3f Y=%+.3f | "
            "BIAS X=%+.3f Y=%+.3f | "
            "STILL=%d\r\n",

            posX,
            posY,

            velX,
            velY,

            correctedAx,
            correctedAy,

            biasX,
            biasY,

            stationary ? 1 : 0
        );
    }
}