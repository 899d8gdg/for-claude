"""A test car and a rig built from it the way the generator builds it in Unreal."""

import math

from interp import InterpBackend, Evaluator, Rig
from car_rig.xmath import Vec, Quat, Xform, X, Y
from car_rig.layout import build_layout
from car_rig.graph import GraphBuilder
from car_rig.solve import build_forward_solve

RADIUS = 33.0
HALF_TRACK = 80.0
HALF_BASE = 135.0


def car_bones(yaw_deg=-90.0, bone_spin=True, extra_pair=False):
    """Rest bones of a car in rig space: facing `yaw_deg`, wheels on the ground.

    The car faces -Y (yaw -90) by default, like a Blender car after import.
    Bones get odd orientations, as glTF imports often do.
    """
    car = Quat.from_yaw(math.radians(yaw_deg))
    odd = Quat.from_axis_angle(Vec(0.3, 1.0, 0.2), 1.1) if bone_spin else Quat()

    def at(f, r, z, rot=odd):
        return Xform(car * rot, car.rotate(Vec(f, r, z)))

    # Rigacar: L is the car's left, which is -Y (right) in Unreal's car frame
    bones = {
        'Armature': Xform(),
        'DEF-Body': at(10.0, 0.0, 60.0),
        'DEF-Wheel.Ft.L': at(HALF_BASE, -HALF_TRACK, RADIUS),
        'DEF-Wheel.Ft.R': at(HALF_BASE, HALF_TRACK, RADIUS),
        'DEF-Wheel.Bk.L': at(-HALF_BASE, -HALF_TRACK, RADIUS),
        'DEF-Wheel.Bk.R': at(-HALF_BASE, HALF_TRACK, RADIUS),
        'DEF-WheelBrake.Ft.L': at(HALF_BASE, -HALF_TRACK + 10.0, RADIUS),
        'DEF-Door.L': at(20.0, -85.0, 80.0),
    }
    if extra_pair:
        bones['DEF-Wheel.Bk.L.001'] = at(-HALF_BASE - 75.0, -HALF_TRACK, RADIUS)
        bones['DEF-Wheel.Bk.R.001'] = at(-HALF_BASE - 75.0, HALF_TRACK, RADIUS)
    parents = {name: 'Armature' for name in bones if name != 'Armature'}
    parents['DEF-Door.L'] = 'DEF-Body'
    parents['DEF-WheelBrake.Ft.L'] = 'DEF-Body'
    return bones, parents, car


class CarRig(object):
    def __init__(self, **kw):
        self.bones, self.parents, self.car_rot = car_bones(**kw)
        self.layout = build_layout(self.bones)
        self.rig = Rig()
        for name in ['Armature'] + [n for n in self.bones if n != 'Armature']:
            parent = self.parents.get(name)
            self.rig.add_global('Bone', name, ('Bone', parent) if parent else None, self.bones[name])
        kinds = {}
        for e in self.layout.elements:
            kind = 'Control' if e.kind == 'control' else 'Null'
            kinds[e.name] = kind
            parent = (kinds[e.parent], e.parent) if e.parent else None
            if kind == 'Control':
                self.rig.add_control(e.name, parent, e.xform)
            else:
                self.rig.add_global('Null', e.name, parent, e.xform)
        self.channel_defaults = {(c.host, c.name): c.default for c in self.layout.channels}
        self.backend = InterpBackend()
        build_forward_solve(GraphBuilder(self.backend), self.layout)
        self.ev = Evaluator(self.backend, self.rig, self.channel_defaults)
        self.rig.ground = lambda x, y: 0.0

    # car frame helpers
    @property
    def fwd(self):
        return self.car_rot.rotate(X)

    @property
    def right(self):
        return self.car_rot.rotate(Y)

    def control(self, name):
        return self.rig.find(('Control', name))

    def set_value(self, name, trans=None, rot=None):
        """Set a control's value in its own (car-aligned) frame."""
        e = self.control(name)
        e.value = Xform(rot or Quat(), trans or Vec())

    def solve(self):
        self.ev.run()

    def bone(self, name):
        return self.rig.global_(self.rig.find(('Bone', name)))

    def rest(self, name):
        return self.bones[name]

    def bone_delta(self, name):
        """Bone motion relative to rest, as a transform applied in rig space."""
        return self.rest(name).inverse() * self.bone(name)
