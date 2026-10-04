"""Small vector, quaternion and transform types that follow Unreal's conventions.

Used to lay out the rig before anything is created in Unreal, and by the tests
to evaluate the generated graph. Conventions match FVector / FQuat / FTransform:
  * Quat(x, y, z, w); a * b applies b first, then a.
  * Xform a * b applies a first, then b (a is expressed in b's space).
  * make_absolute(local, parent) = local * parent
  * make_relative(global, parent) = global * inverse(parent)
"""

import math


class Vec(tuple):
    __slots__ = ()

    def __new__(cls, x=0.0, y=0.0, z=0.0):
        return tuple.__new__(cls, (float(x), float(y), float(z)))

    x = property(lambda self: self[0])
    y = property(lambda self: self[1])
    z = property(lambda self: self[2])

    def __add__(self, o):
        return Vec(self[0] + o[0], self[1] + o[1], self[2] + o[2])

    def __sub__(self, o):
        return Vec(self[0] - o[0], self[1] - o[1], self[2] - o[2])

    def __neg__(self):
        return Vec(-self[0], -self[1], -self[2])

    def __mul__(self, s):
        if isinstance(s, (tuple, list)):
            return Vec(self[0] * s[0], self[1] * s[1], self[2] * s[2])
        return Vec(self[0] * s, self[1] * s, self[2] * s)

    __rmul__ = __mul__

    def __truediv__(self, s):
        return Vec(self[0] / s, self[1] / s, self[2] / s)

    def dot(self, o):
        return self[0] * o[0] + self[1] * o[1] + self[2] * o[2]

    def cross(self, o):
        return Vec(self[1] * o[2] - self[2] * o[1],
                   self[2] * o[0] - self[0] * o[2],
                   self[0] * o[1] - self[1] * o[0])

    def length(self):
        return math.sqrt(self.dot(self))

    def unit(self):
        n = self.length()
        return Vec() if n < 1e-8 else self / n

    def lerp(self, o, t):
        return self + (Vec(*o) - self) * t

    def __repr__(self):
        return 'Vec(%.3f, %.3f, %.3f)' % self


X = Vec(1, 0, 0)
Y = Vec(0, 1, 0)
Z = Vec(0, 0, 1)
ZERO = Vec()
ONE = Vec(1, 1, 1)


class Quat(tuple):
    __slots__ = ()

    def __new__(cls, x=0.0, y=0.0, z=0.0, w=1.0):
        return tuple.__new__(cls, (float(x), float(y), float(z), float(w)))

    x = property(lambda self: self[0])
    y = property(lambda self: self[1])
    z = property(lambda self: self[2])
    w = property(lambda self: self[3])

    @staticmethod
    def from_axis_angle(axis, angle):
        a = Vec(*axis).unit()
        s = math.sin(angle * 0.5)
        return Quat(a.x * s, a.y * s, a.z * s, math.cos(angle * 0.5))

    @staticmethod
    def from_two_vectors(a, b):
        """Shortest rotation taking direction a onto direction b (FQuat::FindBetweenVectors)."""
        a = Vec(*a).unit()
        b = Vec(*b).unit()
        if a.length() < 1e-8 or b.length() < 1e-8:
            return Quat()
        w = 1.0 + a.dot(b)
        if w < 1e-6:
            # opposite directions: rotate 180 degrees about any perpendicular axis
            axis = Vec(-a.z, 0.0, a.x) if abs(a.x) > abs(a.y) else Vec(0.0, -a.z, a.y)
            axis = axis.unit()
            return Quat(axis.x, axis.y, axis.z, 0.0)
        c = a.cross(b)
        return Quat(c.x, c.y, c.z, w).normalized()

    @staticmethod
    def from_yaw(yaw):
        return Quat.from_axis_angle(Z, yaw)

    def normalized(self):
        n = math.sqrt(sum(c * c for c in self))
        if n < 1e-12:
            return Quat()
        return Quat(*(c / n for c in self))

    def __mul__(self, o):
        x1, y1, z1, w1 = self
        x2, y2, z2, w2 = o
        return Quat(w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
                    w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
                    w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
                    w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2)

    def inverse(self):
        return Quat(-self[0], -self[1], -self[2], self[3])

    def rotate(self, v):
        q = Vec(self[0], self[1], self[2])
        v = Vec(*v)
        t = q.cross(v) * 2.0
        return v + t * self[3] + q.cross(t)

    def unrotate(self, v):
        return self.inverse().rotate(v)

    def dot(self, o):
        return sum(a * b for a, b in zip(self, o))

    def slerp(self, o, t):
        """FQuat::Slerp: shortest path, normalized result."""
        cos_omega = self.dot(o)
        o = Quat(*o)
        if cos_omega < 0.0:
            o = Quat(*(-c for c in o))
            cos_omega = -cos_omega
        if cos_omega > 0.9999:
            s0, s1 = 1.0 - t, t
        else:
            omega = math.acos(cos_omega)
            sin_omega = math.sin(omega)
            s0 = math.sin((1.0 - t) * omega) / sin_omega
            s1 = math.sin(t * omega) / sin_omega
        return Quat(*(s0 * a + s1 * b for a, b in zip(self, o))).normalized()

    def angle_to(self, o):
        d = abs(self.normalized().dot(Quat(*o).normalized()))
        return 2.0 * math.acos(min(1.0, d))

    def axis(self, i):
        return self.rotate((X, Y, Z)[i])

    def __repr__(self):
        return 'Quat(%.4f, %.4f, %.4f, %.4f)' % self


class Xform(object):
    __slots__ = ('rot', 'trans', 'scale')

    def __init__(self, rot=None, trans=None, scale=None):
        self.rot = Quat(*rot) if rot is not None else Quat()
        self.trans = Vec(*trans) if trans is not None else Vec()
        self.scale = Vec(*scale) if scale is not None else Vec(1, 1, 1)

    def __mul__(self, parent):
        """self applied first, then parent (FTransform operator*)."""
        rot = parent.rot * self.rot
        scale = self.scale * parent.scale
        trans = parent.rot.rotate(self.trans * parent.scale) + parent.trans
        return Xform(rot, trans, scale)

    def inverse(self):
        inv_rot = self.rot.inverse()
        inv_scale = Vec(*(1.0 / s if abs(s) > 1e-8 else 0.0 for s in self.scale))
        inv_trans = inv_rot.rotate((-self.trans) * inv_scale)
        return Xform(inv_rot, inv_trans, inv_scale)

    def transform_location(self, p):
        return self.rot.rotate(Vec(*p) * self.scale) + self.trans

    def inverse_transform_location(self, p):
        v = self.rot.unrotate(Vec(*p) - self.trans)
        return Vec(*(c / s if abs(s) > 1e-8 else 0.0 for c, s in zip(v, self.scale)))

    def __repr__(self):
        return 'Xform(%r, %r, %r)' % (self.rot, self.trans, self.scale)


def make_absolute(local, parent):
    return local * parent


def make_relative(global_, parent):
    return global_ * parent.inverse()


def yaw_of(v):
    return math.atan2(v[1], v[0])


def horizontal(v):
    return Vec(v[0], v[1], 0.0)
