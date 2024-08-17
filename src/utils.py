import logging
import rtmidi

X32_FADER_SCALE = 0.90 # x32 faders don't quite register the limits of their physical travel.
X32_FADER_RANGE = 127.0
X32_FADER_RANGE_HALF = X32_FADER_RANGE / 2.0

def x32_fader_val_to_db(deflection: int) -> float:
    deflection = (float(deflection - X32_FADER_RANGE_HALF) * X32_FADER_SCALE) + X32_FADER_RANGE_HALF # The physical middle of the fader is 64, so adjust the measured value in reference to that location
    val = (deflection - (X32_FADER_RANGE * 0.75)) / (X32_FADER_RANGE / 16) # 3/4 of the sections are below the 0dB threshold, 16 sections
    if val >= 4.0:
        return 10.0
    if val >= -4.0: # >-10dB
        return val * 2.5
    if val >= -8.0: # >-30dB
        return ((val + 4.0) * 5.0) - 10
    db = ((val + 8.0) * 10.0) - 30
    return db if db > -60.0 else -100.0 # Clamp low db values to -inf (-100)

def x32_db_to_fader_val(db: float) -> int:
    if db >= 10.0:
        return 127
    elif db >= -10.0:
        val = db / 2.5
    elif db >= -30.0:
        val = ((db + 10) / 5.0) - 4.0
    else:
        val = ((db + 30.0) / 10.0) - 8.0
    val = (val * (X32_FADER_RANGE / 16)) + (X32_FADER_RANGE * 0.75)
    deflection = ((val - X32_FADER_RANGE_HALF) / X32_FADER_SCALE) + X32_FADER_RANGE_HALF
    return int(deflection) if deflection > 0.0 else 0
