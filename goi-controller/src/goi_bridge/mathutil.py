"""Tiny dependency-free vector / quaternion helpers. Quaternions are (w, x, y, z)."""
import math


def vdot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def vcross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def vadd(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def vscale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def vlen(a):
    return math.sqrt(vdot(a, a))


def vnormalize(a):
    n = vlen(a)
    if n < 1e-9:
        raise ValueError("cannot normalize a zero vector")
    return (a[0] / n, a[1] / n, a[2] / n)


def angle_between_deg(a, b):
    """Angle between two unit vectors, in degrees."""
    d = max(-1.0, min(1.0, vdot(a, b)))
    return math.degrees(math.acos(d))


def mean_unit_vector(vectors):
    s = (0.0, 0.0, 0.0)
    for v in vectors:
        s = vadd(s, v)
    return vnormalize(s)


def qnormalize(q):
    n = math.sqrt(q[0] ** 2 + q[1] ** 2 + q[2] ** 2 + q[3] ** 2)
    if n < 1e-9:
        raise ValueError("zero quaternion")
    return (q[0] / n, q[1] / n, q[2] / n, q[3] / n)


def qconj(q):
    return (q[0], -q[1], -q[2], -q[3])


def qmul(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw)


def qrotate(q, v):
    """Rotate vector v by unit quaternion q  (v' = q v q*)."""
    w, x, y, z = q
    u = (x, y, z)
    uv = vcross(u, v)
    uuv = vcross(u, uv)
    return vadd(v, vadd(vscale(uv, 2.0 * w), vscale(uuv, 2.0)))


def qrotate_inv(q, v):
    """Rotate v by the inverse of q."""
    return qrotate(qconj(q), v)


def q_from_axis_angle(axis, angle_rad):
    axis = vnormalize(axis)
    s = math.sin(angle_rad / 2.0)
    return (math.cos(angle_rad / 2.0), axis[0] * s, axis[1] * s, axis[2] * s)


def q_from_two_vectors(a, b):
    """Shortest rotation taking unit vector a onto unit vector b."""
    d = vdot(a, b)
    if d > 0.999999:
        return (1.0, 0.0, 0.0, 0.0)
    if d < -0.999999:
        axis = vcross(a, (1.0, 0.0, 0.0))
        if vlen(axis) < 1e-6:
            axis = vcross(a, (0.0, 1.0, 0.0))
        axis = vnormalize(axis)
        return (0.0, axis[0], axis[1], axis[2])
    axis = vcross(a, b)
    return qnormalize((1.0 + d, axis[0], axis[1], axis[2]))


def wrap180(deg):
    """Wrap an angle to [-180, 180)."""
    return (deg + 180.0) % 360.0 - 180.0


def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x
