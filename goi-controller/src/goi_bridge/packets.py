"""Binary packet sent by the ESP32 firmware (little-endian, 40 bytes)."""
import struct
import time
from dataclasses import dataclass
from typing import Optional, Tuple

from .mathutil import qnormalize

MAGIC = 0x4F47      # "GO"
VERSION = 1
# uint16 magic, uint8 version, uint8 battery%, uint32 seq, uint32 t_ms,
# float qw qx qy qz, float ax ay az (linear acceleration, m/s^2, sensor frame)
_STRUCT = struct.Struct("<HBBII4f3f")
PACKET_SIZE = _STRUCT.size


@dataclass
class ImuSample:
    seq: int
    t_ms: int
    battery: int
    q: Tuple[float, float, float, float]      # (w, x, y, z), sensor -> world
    acc: Tuple[float, float, float]
    rx_time: float = 0.0                      # PC monotonic time of arrival


def pack(seq, t_ms, battery, q, acc=(0.0, 0.0, 0.0)) -> bytes:
    return _STRUCT.pack(MAGIC, VERSION, int(battery) & 0xFF, seq & 0xFFFFFFFF,
                        t_ms & 0xFFFFFFFF, q[0], q[1], q[2], q[3], acc[0], acc[1], acc[2])


def parse(data: bytes) -> Optional[ImuSample]:
    if len(data) != PACKET_SIZE:
        return None
    magic, ver, bat, seq, t_ms, w, x, y, z, ax, ay, az = _STRUCT.unpack(data)
    if magic != MAGIC or ver != VERSION:
        return None
    try:
        q = qnormalize((w, x, y, z))
    except ValueError:
        return None
    return ImuSample(seq, t_ms, bat, q, (ax, ay, az), time.monotonic())
