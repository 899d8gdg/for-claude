"""Works out the car rig from the imported bones. Pure Python, no Unreal calls.

Bones follow Rigacar's deformation rig names, as exported from Blender:
    DEF-Body
    DEF-Wheel.Ft.L, DEF-Wheel.Ft.R, DEF-Wheel.Bk.L, DEF-Wheel.Bk.R
    DEF-Wheel.Bk.L.001 ...           (extra wheel pairs)
    DEF-WheelBrake.Ft.L ...          (optional brake callipers, do not spin)
Dots, dashes, underscores and spaces are treated the same, and case is ignored,
so DEF_Wheel_Ft_L also works.

Everything is in rig (component) space, in centimetres, with the ground at Z = 0
in the rest pose, the way the car was modelled in Blender.
"""

import math
import re

from .xmath import Vec, Quat, Xform, X, Y, Z, horizontal

POSITIONS = ('Ft', 'Bk')
SIDES = ('L', 'R')

# Colours of the control groups, like Rigacar's bone groups.
COLOR_DIRECTION = (1.0, 0.75, 0.05)
COLOR_SUSPENSION = (0.65, 0.25, 1.0)
COLOR_WHEEL = (0.1, 0.9, 0.3)
COLOR_SENSOR = (1.0, 0.3, 0.1)

# Root settings, as animation channels on the Root control (Rigacar keeps
# these as custom properties on the armature).
#   name: (default, minimum, maximum)
SETTINGS = (
    ('SuspensionFactor', 0.5, 0.0, 1.0),
    ('SuspensionRollingFactor', 0.5, 0.0, 1.0),
    ('WheelsOnXAxis', 0.0, 0.0, 1.0),
    ('GroundDetection', 1.0, 0.0, 1.0),
    ('GroundSensorLimit', 20.0, 0.0, 500.0),
    ('GroundTraceDepth', 200.0, 0.0, 10000.0),
)

STEERING_CHANNEL = 'Steering_rotation'


class LayoutError(Exception):
    pass


_SEP = re.compile(r'[\s._\-]+')
_WHEEL = re.compile(r'^(def\.)?(wheel|wheelbrake)\.(ft|bk)\.(l|r)(?:\.(\d+))?$')
_BODY = re.compile(r'^(def\.)?body$')


def normalize_name(name):
    return _SEP.sub('.', str(name).strip().lower()).strip('.')


def parse_bone_name(name):
    """('body', None, None, None, has_def) or ('wheel'|'wheelbrake', pos, side, index, has_def) or None."""
    n = normalize_name(name)
    m = _BODY.match(n)
    if m:
        return ('body', None, None, 0, bool(m.group(1)))
    m = _WHEEL.match(n)
    if m:
        pos = 'Ft' if m.group(3) == 'ft' else 'Bk'
        side = m.group(4).upper()
        index = int(m.group(5)) if m.group(5) else 0
        return (m.group(2), pos, side, index, bool(m.group(1)))
    return None


def suffix(pos, side, index=0):
    return '%s_%s' % (pos, side) if index == 0 else '%s_%s_%03d' % (pos, side, index)


class Wheel(object):
    def __init__(self, pos, side, index, bone, center):
        self.pos = pos
        self.side = side
        self.index = index
        self.bone = bone
        self.brake_bone = None
        self.center = center
        self.radius = center.z
        self.suffix = suffix(pos, side, index)

    @property
    def is_front(self):
        return self.pos == 'Ft'

    @property
    def channel(self):
        return 'Wheel_%s_rotation' % self.suffix

    def __repr__(self):
        return 'Wheel(%s, bone=%r, r=%.1f)' % (self.suffix, self.bone, self.radius)


class Element(object):
    """A null or control to create, with its rest transform in rig space."""

    def __init__(self, kind, name, parent, xform, **props):
        self.kind = kind
        self.name = name
        self.parent = parent
        self.xform = xform
        self.control_type = props.pop('control_type', 'EULER_TRANSFORM')
        self.shape = props.pop('shape', 'Default')
        self.color = props.pop('color', (1.0, 1.0, 1.0))
        # shape transform, relative to the control
        self.shape_xform = props.pop('shape_xform', Xform())
        self.minimum = props.pop('minimum', None)
        self.maximum = props.pop('maximum', None)
        self.default = props.pop('default', None)
        self.primary_axis = props.pop('primary_axis', 'X')
        if props:
            raise TypeError('unknown element properties: %s' % ', '.join(props))

    def __repr__(self):
        return '%s(%s <- %s)' % (self.kind, self.name, self.parent)


class Channel(object):
    def __init__(self, name, host, default=0.0, minimum=None, maximum=None):
        self.name = name
        self.host = host
        self.default = default
        self.minimum = minimum
        self.maximum = maximum


class CarLayout(object):
    """Everything the generator and the graph builder need to know about the car."""

    def __init__(self):
        self.body_bone = None
        self.wheels = []
        self.elements = []
        self.channels = []
        self.warnings = []
        self.forward = X
        self.right = Y
        self.car_rot = Quat()
        self.axle_ft = Vec()
        self.axle_bk = Vec()
        self.wheelbase = 0.0
        self.steering_distance = 0.0

    # lookup helpers -------------------------------------------------------
    def wheels_at(self, pos, side=None):
        return [w for w in self.wheels if w.pos == pos and (side is None or w.side == side)]

    def sides_at(self, pos):
        return [s for s in SIDES if self.wheels_at(pos, s)]

    def element(self, name):
        for e in self.elements:
            if e.name == name:
                return e
        raise KeyError(name)

    def add(self, kind, name, parent, xform, **props):
        if any(e.name == name for e in self.elements):
            raise LayoutError('duplicate rig element name %s' % name)
        e = Element(kind, name, parent, xform, **props)
        self.elements.append(e)
        return e

    def to_car(self, p):
        """Rig-space point -> (forward, right, up) offsets relative to the back axle."""
        d = Vec(*p) - self.axle_bk
        return Vec(d.dot(self.forward), d.dot(self.right), d.z)


def find_car_bones(bone_names):
    """Pick the body, wheel and brake bones out of a list of bone names."""
    parsed = [(name, parse_bone_name(name)) for name in bone_names]
    parsed = [(name, p) for name, p in parsed if p]
    # Prefer DEF- bones. Fall back to unprefixed names only when the export
    # has no DEF- bones at all.
    if any(p[4] for _, p in parsed):
        parsed = [(name, p) for name, p in parsed if p[4]]
    body = None
    wheels = {}
    brakes = {}
    for name, (kind, pos, side, index, _) in parsed:
        if kind == 'body':
            body = body or name
        elif kind == 'wheel':
            wheels.setdefault((pos, side, index), name)
        else:
            brakes.setdefault((pos, side, index), name)
    return body, wheels, brakes


def build_layout(bones, bounds=None):
    """Lay out the rig.

    bones:  {bone name: rest global Xform in rig space (cm)}
    bounds: optional (min Vec, max Vec) of the mesh in rig space
    """
    lay = CarLayout()
    body, wheel_bones, brake_bones = find_car_bones(list(bones))
    if not wheel_bones:
        raise LayoutError(
            'No wheel bones found. Expected Rigacar names such as DEF-Wheel.Ft.L, '
            'DEF-Wheel.Ft.R, DEF-Wheel.Bk.L and DEF-Wheel.Bk.R.')
    if not body:
        raise LayoutError('No body bone found. Expected a bone named DEF-Body.')
    lay.body_bone = body

    for (pos, side, index), name in sorted(wheel_bones.items()):
        w = Wheel(pos, side, index, name, bones[name].trans)
        w.brake_bone = brake_bones.get((pos, side, index))
        if w.radius < 1.0:
            lay.warnings.append(
                '%s is at height %.1f cm, so the wheel radius is unknown. '
                'Model the car standing on the ground at Z = 0. Using 30 cm.' % (name, w.radius))
            w.radius = 30.0
        lay.wheels.append(w)

    if not lay.wheels_at('Ft') or not lay.wheels_at('Bk'):
        raise LayoutError('The rig needs at least one front (Ft) and one back (Bk) wheel bone.')

    body_xf = bones[body]
    _car_frame(lay, body_xf.trans)
    dims = _dimensions(lay, body_xf.trans, bounds)
    _build_elements(lay, body_xf, dims)
    return lay


def _mean(points):
    points = list(points)
    s = Vec()
    for p in points:
        s = s + p
    return s / float(len(points))


def _car_frame(lay, body_center):
    ft = _mean(w.center for w in lay.wheels_at('Ft'))
    bk = _mean(w.center for w in lay.wheels_at('Bk'))
    fwd = horizontal(ft - bk).unit()
    if fwd.length() < 0.5:
        raise LayoutError('Front and back wheels are at the same place; cannot tell which way the car faces.')
    lay.forward = fwd
    lay.right = Z.cross(fwd).unit()
    lay.car_rot = Quat.from_yaw(math.atan2(fwd.y, fwd.x))

    def axle_point(pos, pick):
        pts = []
        for side in lay.sides_at(pos):
            ws = lay.wheels_at(pos, side)
            pts.append(pick(ws, key=lambda w: w.center.dot(fwd)).center)
        mid = _mean(pts)
        # put the axle point on the body's centre line, as Rigacar does
        return mid + lay.right * (body_center - mid).dot(lay.right)

    # front axle = frontmost front pair, back axle = rearmost back pair
    lay.axle_ft = axle_point('Ft', max)
    lay.axle_bk = axle_point('Bk', min)
    lay.wheelbase = (lay.axle_ft - lay.axle_bk).length()

    for w in lay.wheels:
        side_sign = (w.center - lay.axle_bk).dot(lay.right)
        if (w.side == 'L') != (side_sign < 0):
            lay.warnings.append(
                '%s is on the car\'s %s side. Left and right are only used for '
                'naming, so the rig still works.' % (w.bone, 'right' if side_sign > 0 else 'left'))
            break


def _dimensions(lay, body_center, bounds):
    """Car extents relative to the back axle: forward, right, up."""
    pts = [lay.to_car(w.center) for w in lay.wheels]
    max_r = max(w.radius for w in lay.wheels)
    if bounds:
        lo, hi = bounds
        corners = [Vec(x, y, z) for x in (lo.x, hi.x) for y in (lo.y, hi.y) for z in (lo.z, hi.z)]
        cpts = [lay.to_car(c) for c in corners]
        front = max(p.x for p in cpts)
        back = min(p.x for p in cpts)
        half_width = max(abs(p.y) for p in cpts)
        height = max(p.z for p in cpts)
    else:
        front = max(p.x for p in pts) + max_r * 1.5
        back = min(p.x for p in pts) - max_r * 1.5
        half_width = max(abs(p.y) for p in pts) + max_r * 0.3
        height = max(body_center.z * 2.0, max_r * 4.0)
    return {
        'front': front, 'back': back, 'half_width': half_width,
        'height': height, 'max_radius': max_r,
        'length': front - back, 'width': half_width * 2.0,
    }


def _xf(p, rot):
    return Xform(rot, p)


def _shape(scale, trans=None, rot=None):
    return Xform(rot or Quat(), trans or Vec(), Vec(scale, scale, scale) if not isinstance(scale, tuple) else scale)


# Default Control Rig shapes are roughly this many cm across at scale 1.
SHAPE_UNIT = 50.0

# Rotations that turn the default shapes, which lie in the XY plane, upright.
_UPRIGHT_SIDE = Quat.from_axis_angle(X, math.pi * 0.5)       # faces the side (wheel plane)


def _build_elements(lay, body_xf, dims):
    rot = lay.car_rot
    fwd, right = lay.forward, lay.right
    L = dims['length']
    W = dims['width']
    max_r = dims['max_radius']

    def at(car_offset):
        """Car-space offset (forward, right, up) from the back axle ground point -> rig space."""
        base = Vec(lay.axle_bk.x, lay.axle_bk.y, 0.0)
        return base + fwd * car_offset[0] + right * car_offset[1] + Z * car_offset[2]

    def car(p):
        c = lay.to_car(p)
        return Vec(c.x, c.y, p.z)

    ft = car(lay.axle_ft)
    bk = car(lay.axle_bk)

    # Root: on the ground under the back axle; its shape outlines the car.
    lay.add('control', 'Root', None, _xf(at((0.0, 0.0, 0.0)), rot),
            shape='Square_Thin', color=COLOR_DIRECTION,
            shape_xform=_shape((L * 1.05 / SHAPE_UNIT, W * 1.1 / SHAPE_UNIT, 1.0),
                               Vec((dims['front'] + dims['back']) * 0.5, 0.0, 1.0)))

    # Drift: pivots the car around the front axle. Shape drawn behind the car.
    drift_pos = at((ft.x, 0.0, bk.z))
    lay.add('control', 'Drift', 'Root', _xf(drift_pos, rot), control_type='ROTATOR',
            shape='Arrow2_Thin', color=COLOR_DIRECTION,
            shape_xform=_shape(W * 0.6 / SHAPE_UNIT, Vec(dims['back'] - ft.x - W * 0.25, 0.0, 0.0)))

    # Axle ground sensors. Front hangs off Root, back off Drift, as in Rigacar.
    sensor_shape = _shape(max_r * 1.4 / SHAPE_UNIT)
    for pos, parent, p in (('Ft', 'Root', ft), ('Bk', 'Drift', bk)):
        xf = _xf(at((p.x, 0.0, p.z)), rot)
        lay.add('null', 'MCH_GroundProjection_Axle_%s' % pos, parent, xf)
        lay.add('control', 'GroundSensor_Axle_%s' % pos, 'MCH_GroundProjection_Axle_%s' % pos, xf,
                control_type='POSITION', shape='Diamond_Thin', color=COLOR_SENSOR,
                shape_xform=_shape(sensor_shape.scale, Vec(0.0, 0.0, -p.z + 1.0)))

    # Axle frames the graph positions every frame.
    lay.add('null', 'MCH_Root_Axle_Bk', 'Drift', _xf(at((bk.x, 0.0, bk.z)), rot))
    lay.add('null', 'MCH_Root_Axle_Ft', 'Root', _xf(at((ft.x, 0.0, ft.z)), rot))

    # Steering: moves sideways in front of the car.
    front_wheels = lay.wheels_at('Ft')
    r_ft = max(w.radius for w in front_wheels)
    lay.steering_distance = max(dims['front'] - ft.x, r_ft) + 4.0 * r_ft
    lay.add('null', 'MCH_Steering_rotation', 'MCH_Root_Axle_Ft', _xf(at((ft.x, 0.0, ft.z)), rot))
    lay.add('control', 'Steering', 'MCH_Steering_rotation',
            _xf(at((ft.x + lay.steering_distance, 0.0, ft.z)), rot),
            control_type='POSITION', shape='Arrow4_Thin', color=COLOR_WHEEL,
            shape_xform=_shape(W * 0.35 / SHAPE_UNIT))
    lay.channels.append(Channel(STEERING_CHANNEL, 'Steering', 0.0))

    # Wheels.
    for w in lay.wheels:
        c = car(w.center)
        xf = _xf(at((c.x, c.y, c.z)), rot)
        out = -1.0 if c.y < 0.0 else 1.0
        lay.add('null', 'MCH_GroundProjection_%s' % w.suffix, 'MCH_Root_Axle_Bk', xf)
        lay.add('control', 'GroundSensor_%s' % w.suffix, 'MCH_GroundProjection_%s' % w.suffix, xf,
                control_type='POSITION', shape='Circle_Thin', color=COLOR_SENSOR,
                shape_xform=_shape(w.radius * 1.6 / SHAPE_UNIT, Vec(0.0, 0.0, -w.radius + 1.0)))
        lay.add('null', 'MCH_Wheel_%s' % w.suffix, 'GroundSensor_%s' % w.suffix, xf)
        lay.add('control', 'Wheel_%s' % w.suffix, 'GroundSensor_%s' % w.suffix,
                _xf(at((c.x, c.y + out * w.radius * 0.45, c.z)), rot),
                control_type='ROTATOR', shape='Circle_Thick', color=COLOR_WHEEL,
                shape_xform=_shape(w.radius * 2.3 / SHAPE_UNIT, rot=_UPRIGHT_SIDE))
        lay.channels.append(Channel(w.channel, 'Wheel_%s' % w.suffix, 0.0))

    # One brake per axle, on the first left wheel (Rigacar's WheelBrake bone).
    for pos in POSITIONS:
        ws = sorted(lay.wheels_at(pos), key=lambda w: (w.side != 'L', w.index))
        w = ws[0]
        c = car(w.center)
        out = -1.0 if c.y < 0.0 else 1.0
        lay.add('control', 'WheelBrake_%s' % pos, 'GroundSensor_%s' % w.suffix,
                _xf(at((c.x, c.y + out * w.radius * 0.6, c.z + w.radius * 1.2)), rot),
                control_type='FLOAT', shape='Square_Thick', color=COLOR_WHEEL,
                minimum=0.0, maximum=1.0, default=0.0, primary_axis='Z',
                shape_xform=_shape(w.radius * 0.3 / SHAPE_UNIT))

    # Dampers: one per axle side. They tilt the body but leave the wheels alone.
    for pos in POSITIONS:
        for side in lay.sides_at(pos):
            ws = lay.wheels_at(pos, side)
            m = car(_mean(w.center for w in ws))
            h = m.z
            out = -1.0 if m.y < 0.0 else 1.0
            outer = max(abs(car(w.center).y) for w in ws) * out
            xf = _xf(at((m.x, m.y, m.z)), rot)
            name = '%s_%s' % (pos, side)
            lay.add('null', 'MCH_GroundSensor_%s' % name, 'MCH_Root_Axle_Bk', xf)
            lay.add('control', 'WheelDamper_%s' % name, 'MCH_GroundSensor_%s' % name,
                    _xf(at((m.x, outer + out * h * 0.25, h * 1.5)), rot),
                    control_type='POSITION', shape='Triangle_Thick', color=COLOR_SUSPENSION,
                    shape_xform=_shape(h * 0.5 / SHAPE_UNIT, rot=_UPRIGHT_SIDE))
            lay.add('null', 'MCH_WheelDamper_%s' % name, 'WheelDamper_%s' % name, xf)

    # Suspension frames: one point per axle on the centre line.
    for pos in POSITIONS:
        m = car(_mean(_mean(w.center for w in lay.wheels_at(pos, s)) for s in lay.sides_at(pos)))
        lay.add('null', 'MCH_Suspension_%s' % pos, 'MCH_Root_Axle_Bk', _xf(at((m.x, 0.0, m.z)), rot))
    s_ft = lay.element('MCH_Suspension_Ft').xform
    lay.add('null', 'MCH_Axis', 'MCH_Root_Axle_Bk', _xf(s_ft.trans, rot))
    lay.add('null', 'MCH_Body', 'MCH_Axis', Xform(body_xf.rot, body_xf.trans))

    bc = car(body_xf.trans)
    lay.add('control', 'Suspension', 'MCH_Axis',
            _xf(at((bc.x, 0.0, dims['height'] + W * 0.25)), rot),
            control_type='POSITION', shape='Box_Thin', color=COLOR_SUSPENSION,
            shape_xform=_shape(W * 0.2 / SHAPE_UNIT))

    for name, default, lo, hi in SETTINGS:
        lay.channels.append(Channel(name, 'Root', default, lo, hi))
