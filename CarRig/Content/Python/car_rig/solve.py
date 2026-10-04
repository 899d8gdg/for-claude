"""The car rig's Forward Solve graph.

This is the Unreal version of the constraints Rigacar puts on its bones. Each
MCH_ null is placed by the graph every frame, top to bottom, and the controls
under it follow through the hierarchy:

  1. Axle ground sensors are projected onto the ground (Rigacar's shrinkwrap).
     The front one is kept one wheelbase away from the back one, and the back
     axle frame aims at the front axle, so the car pitches on slopes.
  2. Steering: the front wheels aim at the Steering control, moved sideways by
     the baked Steering_rotation channel. The Steering control does not turn
     with Drift, so the front wheels counter-steer while the body drifts.
  3. Each wheel's ground sensor is projected onto the ground, clamped to
     GroundSensorLimit, and the wheel spins from its Wheel control, its baked
     rotation channel and, if WheelsOnXAxis is on, the Root's forward motion.
  4. Dampers and ground sensors tilt the body, scaled by SuspensionFactor and
     SuspensionRollingFactor, and the Suspension control adds pitch, roll and
     bounce.
  5. The DEF bones copy their MCH nulls.
"""

import math

from .graph import Key, Enum
from .layout import POSITIONS
from .xmath import Vec, Quat, X, Y, ZERO, ONE

GLOBAL = Enum('GlobalSpace')
LOCAL = Enum('LocalSpace')

# Suspension control: travel (cm) that gives the full effect, as in Rigacar
# (2 m for 6 / 7 degrees of pitch / roll, 0.5 m for 0.1 m of bounce).
SUSPENSION_TRAVEL = 200.0
SUSPENSION_PITCH = math.radians(6.0)
SUSPENSION_ROLL = math.radians(7.0)
SUSPENSION_BOUNCE_TRAVEL = 50.0
SUSPENSION_BOUNCE = 10.0

TRACE_RADIUS = 1.0


class Ops(object):
    """Graph helpers. Every method returns an output Pin."""

    def __init__(self, builder):
        self.b = builder
        self._memo = {}

    # hierarchy -------------------------------------------------------------
    def _key(self, name, kind):
        return Key(kind, name)

    def get(self, name, kind='Null', space=GLOBAL, initial=False):
        memo = (name, kind, space.value, initial, None if initial else self.b.version)
        node = self._memo.get(memo)
        if node is None:
            node = self.b.unit('RigUnit_GetTransform', Item=Key(kind, name), Space=space, bInitial=initial)
            self._memo[memo] = node
        return node['Transform']

    def ctrl(self, name, **kw):
        return self.get(name, 'Control', **kw)

    def set(self, name, value, kind='Null', space=GLOBAL):
        self.b.mutable('RigUnit_SetTransform', Item=Key(kind, name), Space=space, bInitial=False,
                       Value=value, Weight=1.0, bPropagateToChildren=True)

    def channel(self, host, name):
        memo = ('channel', host, name)
        node = self._memo.get(memo)
        if node is None:
            node = self.b.unit('RigUnit_GetFloatAnimationChannel', Control=host, Channel=name, bInitial=False)
            self._memo[memo] = node
        return node['Value']

    # transforms ------------------------------------------------------------
    def absolute(self, local, parent):
        return self.b.unit('RigVMFunction_MathTransformMakeAbsolute', Local=local, Parent=parent)['Global']

    def relative(self, global_, parent):
        return self.b.unit('RigVMFunction_MathTransformMakeRelative', Global=global_, Parent=parent)['Local']

    def xform(self, translation=ZERO, rotation=None):
        return self.b.unit('RigVMFunction_MathTransformMake', Translation=translation,
                           Rotation=rotation if rotation is not None else Quat(), Scale=ONE)['Result']

    # vectors ---------------------------------------------------------------
    def add(self, a, b):
        return self.b.unit('RigVMFunction_MathVectorAdd', A=a, B=b)['Result']

    def sub(self, a, b):
        return self.b.unit('RigVMFunction_MathVectorSub', A=a, B=b)['Result']

    def scale(self, v, f):
        return self.b.unit('RigVMFunction_MathVectorScale', Value=v, Factor=f)['Result']

    def unit(self, v):
        return self.b.unit('RigVMFunction_MathVectorUnit', Value=v)['Result']

    def lerp(self, a, b, t):
        return self.b.unit('RigVMFunction_MathVectorLerp', A=a, B=b, T=t)['Result']

    def vec(self, x=0.0, y=0.0, z=0.0):
        return self.b.unit('RigVMFunction_MathVectorMake', X=x, Y=y, Z=z)['Result']

    # rotations -------------------------------------------------------------
    def qmul(self, a, b):
        return self.b.unit('RigVMFunction_MathQuaternionMul', A=a, B=b)['Result']

    def qaxis(self, axis, angle):
        return self.b.unit('RigVMFunction_MathQuaternionFromAxisAndAngle', Axis=axis, Angle=angle)['Result']

    def qbetween(self, a, b):
        return self.b.unit('RigVMFunction_MathQuaternionFromTwoVectors', A=a, B=b)['Result']

    def qrotate(self, q, v):
        return self.b.unit('RigVMFunction_MathQuaternionRotateVector', Transform=q, Vector=v)['Result']

    def qslerp(self, a, b, t):
        return self.b.unit('RigVMFunction_MathQuaternionSlerp', A=a, B=b, T=t)['Result']

    def qinv(self, q):
        return self.b.unit('RigVMFunction_MathQuaternionInverse', Value=q)['Result']

    def length(self, v):
        return self.b.unit('RigVMFunction_MathVectorLength', Value=v)['Result']

    # floats ----------------------------------------------------------------
    def fadd(self, a, b):
        return self.b.unit('RigVMFunction_MathFloatAdd', A=a, B=b)['Result']

    def fsub(self, a, b):
        return self.b.unit('RigVMFunction_MathFloatSub', A=a, B=b)['Result']

    def fmul(self, a, b):
        return self.b.unit('RigVMFunction_MathFloatMul', A=a, B=b)['Result']

    def fneg(self, a):
        return self.fmul(a, -1.0)

    def clamp(self, v, lo, hi):
        return self.b.unit('RigVMFunction_MathFloatClamp', Value=v, Minimum=lo, Maximum=hi)['Result']

    def remap(self, v, s0, s1, t0, t1):
        return self.b.unit('RigVMFunction_MathFloatRemap', Value=v, SourceMinimum=s0, SourceMaximum=s1,
                           TargetMinimum=t0, TargetMaximum=t1, bClamp=True)['Result']

    def bool_to_float(self, v):
        return self.b.unit('RigVMFunction_MathBoolToFloat', Value=v)['Result']

    def trace(self, start, end):
        return self.b.unit('RigUnit_SphereTraceByTraceChannel', Start=start, End=end, Radius=TRACE_RADIUS)


def build_forward_solve(builder, lay):
    o = Ops(builder)
    builder.begin()

    detect = o.channel('Root', 'GroundDetection')
    limit = o.channel('Root', 'GroundSensorLimit')
    depth = o.channel('Root', 'GroundTraceDepth')

    def project(null, parent_global, height, clamp_limit):
        """Place `null` on the ground below its rest position under `parent_global`.

        height: rest height of the null above the ground (wheel radius).
        Returns the null's new global transform.
        """
        g = o.absolute(o.get(null, initial=True, space=LOCAL), parent_global)
        p = g['Translation']
        start = o.add(p, Vec(0, 0, height))
        end = o.sub(p, o.vec(z=o.fadd(height, depth)))
        hit = o.trace(start, end)
        dz = o.fsub(o.fadd(hit['HitLocation.Z'], height), p['Z'])
        if clamp_limit:
            dz = o.clamp(dz, o.fneg(limit), limit)
        on = o.fmul(o.bool_to_float(hit['bHit']), detect)
        dz = o.fmul(dz, on)
        return o.add(p, o.vec(z=dz)), g['Rotation']

    # 1. axles --------------------------------------------------------------
    ft_h = lay.element('GroundSensor_Axle_Ft').xform.trans.z
    bk_h = lay.element('GroundSensor_Axle_Bk').xform.trans.z

    p, r = project('MCH_GroundProjection_Axle_Ft', o.ctrl('Root'), ft_h, False)
    o.set('MCH_GroundProjection_Axle_Ft', o.xform(p, r))
    p, r = project('MCH_GroundProjection_Axle_Bk', o.ctrl('Drift'), bk_h, False)
    o.set('MCH_GroundProjection_Axle_Bk', o.xform(p, r))

    p_ft_free = o.ctrl('GroundSensor_Axle_Ft')['Translation']
    p_bk = o.ctrl('GroundSensor_Axle_Bk')['Translation']
    axle_dir = o.unit(o.sub(p_ft_free, p_bk))
    p_ft = o.add(p_bk, o.scale(axle_dir, lay.wheelbase))

    q_drift = o.ctrl('Drift')['Rotation']
    q_base = o.qmul(o.qbetween(o.qrotate(q_drift, X), axle_dir), q_drift)
    o.set('MCH_Root_Axle_Bk', o.xform(p_bk, q_base))

    # The front axle frame (and the Steering control under it) follows the
    # Root and the slope, but not Drift.
    q_root = o.ctrl('Root')['Rotation']
    d = o.qrotate(o.qinv(q_root), axle_dir)
    flat = o.vec(o.length(o.vec(d['X'], d['Y'], 0.0)), 0.0, d['Z'])
    q_front = o.qmul(q_root, o.qbetween(X, flat))
    o.set('MCH_Root_Axle_Ft', o.xform(p_ft, q_front))

    # 2. steering -----------------------------------------------------------
    # The front wheels point at the Steering control whatever Drift does, so
    # they counter-steer while the body drifts (Rigacar's "Drift counter
    # animation").
    baked = o.channel('Steering', 'Steering_rotation')
    o.set('MCH_Steering_rotation', o.xform(o.vec(y=baked)), space=LOCAL)
    steer = o.relative(o.ctrl('Steering'), o.get('MCH_Root_Axle_Ft'))['Translation']
    aim = o.qrotate(q_front, o.unit(o.vec(steer['X'], steer['Y'], 0.0)))
    a = o.qrotate(o.qinv(q_base), aim)
    q_steer = o.qbetween(X, o.unit(o.vec(a['X'], a['Y'], 0.0)))

    # 3. wheels -------------------------------------------------------------
    base = o.get('MCH_Root_Axle_Bk')
    root_value = o.relative(o.ctrl('Root'), o.ctrl('Root', initial=True))['Translation']
    on_x = o.channel('Root', 'WheelsOnXAxis')
    for w in lay.wheels:
        null = 'MCH_GroundProjection_%s' % w.suffix
        p, r = project(null, base, w.center.z, True)
        if w.is_front:
            r = o.qmul(r, q_steer)
        o.set(null, o.xform(p, r))

    for w in lay.wheels:
        sensor = 'GroundSensor_%s' % w.suffix
        angle = o.channel('Wheel_%s' % w.suffix, w.channel)
        angle = o.fadd(angle, o.fmul(on_x, o.fmul(root_value['X'], 1.0 / w.radius)))
        q_manual = o.relative(o.ctrl('Wheel_%s' % w.suffix), o.ctrl(sensor))['Rotation']
        q_spin = o.qmul(o.qaxis(Y, angle), q_manual)
        rest = o.get('MCH_Wheel_%s' % w.suffix, space=LOCAL, initial=True)
        o.set('MCH_Wheel_%s' % w.suffix, o.xform(rest['Translation'], o.qmul(rest['Rotation'], q_spin)),
              space=LOCAL)

    # 4. dampers and body ---------------------------------------------------
    base = o.get('MCH_Root_Axle_Bk')
    q_base = base['Rotation']
    for pos in POSITIONS:
        for side in lay.sides_at(pos):
            ws = lay.wheels_at(pos, side)
            pts = [o.ctrl('GroundSensor_%s' % w.suffix)['Translation'] for w in ws]
            mid = pts[0]
            for i, pt in enumerate(pts[1:], 2):
                mid = o.lerp(mid, pt, 1.0 / i)
            o.set('MCH_GroundSensor_%s_%s' % (pos, side), o.xform(mid, q_base))

    factor = o.channel('Root', 'SuspensionFactor')
    rolling = o.channel('Root', 'SuspensionRollingFactor')
    base = o.get('MCH_Root_Axle_Bk')
    q_base = base['Rotation']
    free = {}
    susp = {}
    rolls = {}
    for pos in POSITIONS:
        sides = lay.sides_at(pos)
        dampers = {s: o.get('MCH_WheelDamper_%s_%s' % (pos, s))['Translation'] for s in sides}
        if len(sides) == 2:
            mid = o.lerp(dampers['L'], dampers['R'], 0.5)
            rest_l = lay.element('MCH_WheelDamper_%s_L' % pos).xform.trans
            rest_r = lay.element('MCH_WheelDamper_%s_R' % pos).xform.trans
            rest_dir = lay.car_rot.unrotate((rest_l - rest_r).unit())
            now_dir = o.unit(o.sub(dampers['L'], dampers['R']))
            rolls[pos] = o.qbetween(o.qrotate(q_base, rest_dir), now_dir)
        else:
            mid = dampers[sides[0]]
        u = o.absolute(o.get('MCH_Suspension_%s' % pos, space=LOCAL, initial=True), base)['Translation']
        free[pos] = u
        susp[pos] = o.add(u, o.vec(z=o.fmul(factor, o.fsub(mid['Z'], u['Z']))))

    q_axis = o.qmul(o.qbetween(o.unit(o.sub(free['Ft'], free['Bk'])),
                               o.unit(o.sub(susp['Ft'], susp['Bk']))), q_base)
    # Rigacar blends the front axle roll in at SuspensionRollingFactor, then
    # the back axle roll at half that.
    q_roll = Quat()
    if 'Ft' in rolls:
        q_roll = o.qslerp(q_roll, rolls['Ft'], rolling)
    if 'Bk' in rolls:
        q_roll = o.qslerp(q_roll, rolls['Bk'], o.fmul(rolling, 0.5))
    if rolls:
        q_axis = o.qmul(q_roll, q_axis)
    o.set('MCH_Axis', o.xform(susp['Ft'], q_axis))

    axis = o.get('MCH_Axis')
    s_now = o.relative(o.ctrl('Suspension'), axis)['Translation']
    s_rest = lay.car_rot.unrotate(lay.element('Suspension').xform.trans - lay.element('MCH_Axis').xform.trans)
    sv = o.sub(s_now, s_rest)
    pitch = o.remap(sv['X'], -SUSPENSION_TRAVEL, SUSPENSION_TRAVEL, -SUSPENSION_PITCH, SUSPENSION_PITCH)
    roll = o.remap(sv['Y'], -SUSPENSION_TRAVEL, SUSPENSION_TRAVEL, SUSPENSION_ROLL, -SUSPENSION_ROLL)
    bounce = o.remap(sv['Z'], -SUSPENSION_BOUNCE_TRAVEL, SUSPENSION_BOUNCE_TRAVEL,
                     -SUSPENSION_BOUNCE, SUSPENSION_BOUNCE)
    q_susp = o.qmul(o.qaxis(Y, pitch), o.qaxis(X, roll))
    body_rest = o.get('MCH_Body', space=LOCAL, initial=True)
    o.set('MCH_Body', o.xform(o.add(body_rest['Translation'], o.vec(z=bounce)),
                              o.qmul(q_susp, body_rest['Rotation'])), space=LOCAL)

    # 5. bones --------------------------------------------------------------
    def follow(bone, driver, driver_kind='Null'):
        offset = o.relative(o.get(bone, 'Bone', initial=True), o.get(driver, driver_kind, initial=True))
        o.set(bone, o.absolute(offset, o.get(driver, driver_kind)), kind='Bone')

    follow(lay.body_bone, 'MCH_Body')
    for w in lay.wheels:
        follow(w.bone, 'MCH_Wheel_%s' % w.suffix)
    for w in lay.wheels:
        if w.brake_bone:
            follow(w.brake_bone, 'GroundSensor_%s' % w.suffix, 'Control')
    return builder
