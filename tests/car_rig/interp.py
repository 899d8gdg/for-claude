"""Evaluates car rig graphs in plain Python, the way Control Rig would.

InterpBackend records the nodes GraphBuilder creates. Rig holds a hierarchy of
bones, nulls and controls. run() executes the forward solve: mutable nodes in
execution order, pure nodes once each, just before the first node that needs
them (RigVM's behaviour).
"""

import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'CarRig', 'Content', 'Python'))

from car_rig.xmath import Vec, Quat, Xform, make_absolute, make_relative, ONE  # noqa: E402
from car_rig.graph import Key, Enum  # noqa: E402

# struct -> (inputs, outputs). Pin names are the C++ names.
NODE_SPECS = {
    'RigUnit_BeginExecution': ((), ()),
    'RigUnit_GetTransform': (('Item', 'Space', 'bInitial'), ('Transform',)),
    'RigUnit_SetTransform': (('Item', 'Space', 'bInitial', 'Value', 'Weight', 'bPropagateToChildren'), ()),
    'RigUnit_GetFloatAnimationChannel': (('Control', 'Channel', 'bInitial'), ('Value',)),
    'RigUnit_SphereTraceByTraceChannel': (('Start', 'End', 'Radius', 'TraceChannel'),
                                          ('bHit', 'HitLocation', 'HitNormal')),
    'RigVMFunction_MathTransformMakeAbsolute': (('Local', 'Parent'), ('Global',)),
    'RigVMFunction_MathTransformMakeRelative': (('Global', 'Parent'), ('Local',)),
    'RigVMFunction_MathTransformMake': (('Translation', 'Rotation', 'Scale'), ('Result',)),
    'RigVMFunction_MathVectorAdd': (('A', 'B'), ('Result',)),
    'RigVMFunction_MathVectorSub': (('A', 'B'), ('Result',)),
    'RigVMFunction_MathVectorScale': (('Value', 'Factor'), ('Result',)),
    'RigVMFunction_MathVectorUnit': (('Value',), ('Result',)),
    'RigVMFunction_MathVectorLerp': (('A', 'B', 'T'), ('Result',)),
    'RigVMFunction_MathVectorMake': (('X', 'Y', 'Z'), ('Result',)),
    'RigVMFunction_MathQuaternionMul': (('A', 'B'), ('Result',)),
    'RigVMFunction_MathQuaternionFromAxisAndAngle': (('Axis', 'Angle'), ('Result',)),
    'RigVMFunction_MathQuaternionFromTwoVectors': (('A', 'B'), ('Result',)),
    'RigVMFunction_MathQuaternionRotateVector': (('Transform', 'Vector'), ('Result',)),
    'RigVMFunction_MathQuaternionSlerp': (('A', 'B', 'T'), ('Result',)),
    'RigVMFunction_MathQuaternionInverse': (('Value',), ('Result',)),
    'RigVMFunction_MathVectorLength': (('Value',), ('Result',)),
    'RigVMFunction_MathFloatAdd': (('A', 'B'), ('Result',)),
    'RigVMFunction_MathFloatSub': (('A', 'B'), ('Result',)),
    'RigVMFunction_MathFloatMul': (('A', 'B'), ('Result',)),
    'RigVMFunction_MathFloatClamp': (('Value', 'Minimum', 'Maximum'), ('Result',)),
    'RigVMFunction_MathFloatRemap': (('Value', 'SourceMinimum', 'SourceMaximum', 'TargetMinimum',
                                      'TargetMaximum', 'bClamp'), ('Result',)),
    'RigVMFunction_MathBoolToFloat': (('Value',), ('Result',)),
}

DEFAULTS = {
    'Weight': 1.0, 'bPropagateToChildren': True, 'bInitial': False, 'Space': Enum('GlobalSpace'),
    'Scale': ONE, 'Rotation': Quat(), 'Translation': Vec(), 'Radius': 0.0, 'bClamp': False,
}


class GraphSpecError(Exception):
    pass


class INode(object):
    def __init__(self, struct, name):
        self.struct = struct
        self.name = name
        self.defaults = {}
        self.links = {}
        self.exec_next = None


class InterpBackend(object):
    def __init__(self):
        self.nodes = []
        self.begin = None

    def add_unit(self, path, position):
        struct = path.rsplit('.', 1)[1]
        if struct not in NODE_SPECS:
            raise GraphSpecError('unknown node type %s' % struct)
        node = INode(struct, 'N%03d_%s' % (len(self.nodes), struct))
        self.nodes.append(node)
        if struct == 'RigUnit_BeginExecution':
            self.begin = node
        return node, node.name

    def _check(self, node, pin, output):
        root = pin.split('.')[0]
        ins, outs = NODE_SPECS[node.struct]
        if root not in (outs if output else ins):
            raise GraphSpecError('%s has no %s pin %s' % (node.struct, 'output' if output else 'input', root))

    def set_default(self, node, pin, value):
        self._check(node, pin, False)
        node.defaults[pin] = value

    def link(self, src, src_pin, dst, dst_pin):
        self._check(src, src_pin, True)
        self._check(dst, dst_pin, False)
        if dst_pin in dst.links:
            raise GraphSpecError('%s.%s linked twice' % (dst.name, dst_pin))
        dst.links[dst_pin] = (src, src_pin)

    def link_exec(self, src, dst):
        if src.exec_next is not None:
            raise GraphSpecError('%s already has an execution link' % src.name)
        src.exec_next = dst


# ---------------------------------------------------------------------------
# hierarchy

class Element(object):
    def __init__(self, kind, name, parent):
        self.kind = kind
        self.name = name
        self.parent = parent      # (kind, name) or None
        self.initial_local = Xform()
        self.local = Xform()      # bones / nulls: relative to parent
        self.offset = Xform()     # controls: offset relative to parent
        self.value = Xform()      # controls: value relative to offset
        self.initial_value = Xform()


class Rig(object):
    def __init__(self):
        self.elements = {}
        self.order = []
        self.channels = {}        # (host, name) -> value
        self.ground = None        # f(x, y) -> ground height or None

    def add(self, kind, name, parent=None):
        e = Element(kind, name, parent)
        self.elements[(kind, name)] = e
        self.order.append(e)
        return e

    def find(self, key):
        if isinstance(key, Key):
            key = (key.type, key.name)
        if key not in self.elements:
            raise KeyError('no element %s %s' % key)
        return self.elements[key]

    def parent_global(self, e, initial):
        if e.parent is None:
            return Xform()
        return self.global_(self.find(e.parent), initial)

    def local_(self, e, initial):
        if e.kind == 'Control':
            return (e.initial_value if initial else e.value) * e.offset
        return e.initial_local if initial else e.local

    def global_(self, e, initial=False):
        return self.local_(e, initial) * self.parent_global(e, initial)

    def set_global(self, e, xf):
        parent = self.parent_global(e, False)
        if e.kind == 'Control':
            e.value = make_relative(xf, e.offset * parent)
        else:
            e.local = make_relative(xf, parent)

    def set_local(self, e, xf):
        if e.kind == 'Control':
            e.value = make_relative(xf, e.offset)
        else:
            e.local = xf

    def reset(self):
        for e in self.order:
            e.local = e.initial_local
            e.value = e.initial_value

    # building helpers ------------------------------------------------------
    def add_global(self, kind, name, parent, xf):
        """Add a bone or null at a global rest transform."""
        e = self.add(kind, name, parent)
        p = self.parent_global(e, True)
        e.initial_local = e.local = make_relative(xf, p)
        return e

    def add_control(self, name, parent, offset_global):
        e = self.add('Control', name, parent)
        p = self.parent_global(e, True)
        e.offset = make_relative(offset_global, p)
        return e


# ---------------------------------------------------------------------------
# evaluation

def _sub(value, path):
    for part in path.split('.')[1:]:
        if isinstance(value, Xform):
            value = {'Translation': value.trans, 'Rotation': value.rot, 'Scale3D': value.scale}[part]
        elif isinstance(value, (Vec, Quat)):
            value = getattr(value, part.lower())
        else:
            raise GraphSpecError('cannot read %s of %r' % (part, value))
    return value


class Evaluator(object):
    def __init__(self, backend, rig, channel_defaults=None):
        self.backend = backend
        self.rig = rig
        self.channel_defaults = channel_defaults or {}
        self.cache = {}

    def value(self, node, pin):
        if pin in node.links:
            src, src_pin = node.links[pin]
            outputs = self.pure(src)
            return _sub(outputs[src_pin.split('.')[0]], src_pin)
        if pin in node.defaults:
            return node.defaults[pin]
        if pin in DEFAULTS:
            return DEFAULTS[pin]
        raise GraphSpecError('%s.%s has no value' % (node.name, pin))

    def pure(self, node):
        if node.name not in self.cache:
            self.cache[node.name] = self.compute(node)
        return self.cache[node.name]

    def run(self):
        self.cache = {}
        node = self.backend.begin
        steps = 0
        while node is not None:
            self.compute(node)
            node = node.exec_next
            steps += 1
        return steps

    def compute(self, n):
        v = lambda pin: self.value(n, pin)  # noqa: E731
        s = n.struct
        rig = self.rig
        if s == 'RigUnit_BeginExecution':
            return {}
        if s == 'RigUnit_GetTransform':
            e = rig.find(v('Item'))
            initial = v('bInitial')
            if v('Space').value == 'GlobalSpace':
                return {'Transform': rig.global_(e, initial)}
            return {'Transform': rig.local_(e, initial)}
        if s == 'RigUnit_SetTransform':
            e = rig.find(v('Item'))
            assert v('Weight') == 1.0 and v('bPropagateToChildren') is True and v('bInitial') is False
            if v('Space').value == 'GlobalSpace':
                rig.set_global(e, v('Value'))
            else:
                rig.set_local(e, v('Value'))
            return {}
        if s == 'RigUnit_GetFloatAnimationChannel':
            k = (v('Control'), v('Channel'))
            if k in rig.channels:
                return {'Value': rig.channels[k]}
            if k not in self.channel_defaults:
                raise GraphSpecError('no animation channel %s on %s' % (k[1], k[0]))
            return {'Value': self.channel_defaults[k]}
        if s == 'RigUnit_SphereTraceByTraceChannel':
            a, b = v('Start'), v('End')
            assert abs(a.x - b.x) < 1e-6 and abs(a.y - b.y) < 1e-6, 'traces are vertical'
            gz = rig.ground(a.x, a.y) if rig.ground else None
            if gz is not None and b.z <= gz <= a.z:
                return {'bHit': True, 'HitLocation': Vec(a.x, a.y, gz), 'HitNormal': Vec(0, 0, 1)}
            return {'bHit': False, 'HitLocation': Vec(), 'HitNormal': Vec()}
        if s == 'RigVMFunction_MathTransformMakeAbsolute':
            return {'Global': make_absolute(v('Local'), v('Parent'))}
        if s == 'RigVMFunction_MathTransformMakeRelative':
            return {'Local': make_relative(v('Global'), v('Parent'))}
        if s == 'RigVMFunction_MathTransformMake':
            return {'Result': Xform(v('Rotation'), v('Translation'), v('Scale'))}
        if s == 'RigVMFunction_MathVectorAdd':
            return {'Result': Vec(*v('A')) + v('B')}
        if s == 'RigVMFunction_MathVectorSub':
            return {'Result': Vec(*v('A')) - v('B')}
        if s == 'RigVMFunction_MathVectorScale':
            return {'Result': Vec(*v('Value')) * v('Factor')}
        if s == 'RigVMFunction_MathVectorUnit':
            return {'Result': Vec(*v('Value')).unit()}
        if s == 'RigVMFunction_MathVectorLerp':
            return {'Result': Vec(*v('A')).lerp(v('B'), v('T'))}
        if s == 'RigVMFunction_MathVectorMake':
            return {'Result': Vec(v('X'), v('Y'), v('Z'))}
        if s == 'RigVMFunction_MathQuaternionMul':
            return {'Result': Quat(*v('A')) * Quat(*v('B'))}
        if s == 'RigVMFunction_MathQuaternionFromAxisAndAngle':
            return {'Result': Quat.from_axis_angle(v('Axis'), v('Angle'))}
        if s == 'RigVMFunction_MathQuaternionFromTwoVectors':
            return {'Result': Quat.from_two_vectors(v('A'), v('B'))}
        if s == 'RigVMFunction_MathQuaternionRotateVector':
            return {'Result': Quat(*v('Transform')).rotate(v('Vector'))}
        if s == 'RigVMFunction_MathQuaternionSlerp':
            return {'Result': Quat(*v('A')).slerp(v('B'), v('T'))}
        if s == 'RigVMFunction_MathQuaternionInverse':
            return {'Result': Quat(*v('Value')).inverse()}
        if s == 'RigVMFunction_MathVectorLength':
            return {'Result': Vec(*v('Value')).length()}
        if s == 'RigVMFunction_MathFloatAdd':
            return {'Result': v('A') + v('B')}
        if s == 'RigVMFunction_MathFloatSub':
            return {'Result': v('A') - v('B')}
        if s == 'RigVMFunction_MathFloatMul':
            return {'Result': v('A') * v('B')}
        if s == 'RigVMFunction_MathFloatClamp':
            return {'Result': min(max(v('Value'), v('Minimum')), v('Maximum'))}
        if s == 'RigVMFunction_MathFloatRemap':
            x, s0, s1, t0, t1 = (v(p) for p in ('Value', 'SourceMinimum', 'SourceMaximum',
                                                'TargetMinimum', 'TargetMaximum'))
            r = (x - s0) / (s1 - s0) if s1 != s0 else 0.0
            if v('bClamp'):
                r = min(max(r, 0.0), 1.0)
            return {'Result': t0 + (t1 - t0) * r}
        if s == 'RigVMFunction_MathBoolToFloat':
            return {'Result': 1.0 if v('Value') else 0.0}
        raise GraphSpecError('no evaluator for %s' % s)


def angle_deg(q):
    return math.degrees(Quat().angle_to(q))
