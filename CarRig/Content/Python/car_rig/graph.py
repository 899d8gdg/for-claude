"""A small builder for Control Rig graphs.

The forward-solve graph is described with GraphBuilder. A backend turns it into
real nodes: UnrealBackend talks to a RigVMController inside the editor, and the
tests use an interpreter backend that evaluates the same graph in plain Python.
"""

from .xmath import Vec, Quat, Xform

CONTROL_RIG_UNITS = '/Script/ControlRig.'
RIGVM_UNITS = '/Script/RigVM.'

COLUMN_WIDTH = 420.0
ROW_HEIGHT = 260.0


class Key(object):
    """A rig element key literal: Key('Bone', 'DEF-Body')."""
    __slots__ = ('type', 'name')

    def __init__(self, type_, name):
        self.type = type_
        self.name = name

    def __repr__(self):
        return 'Key(%s, %s)' % (self.type, self.name)


class Enum(object):
    __slots__ = ('value',)

    def __init__(self, value):
        self.value = value


class Pin(object):
    __slots__ = ('node', 'path')

    def __init__(self, node, path):
        self.node = node
        self.path = path

    def __getitem__(self, sub):
        return Pin(self.node, self.path + '.' + sub)

    def __repr__(self):
        return 'Pin(%s.%s)' % (self.node.name, self.path)


class Node(object):
    __slots__ = ('struct', 'handle', 'name', 'column', 'mutable')

    def __init__(self, struct, handle, name, column, mutable):
        self.struct = struct
        self.handle = handle
        self.name = name
        self.column = column
        self.mutable = mutable

    def __getitem__(self, pin):
        return Pin(self, pin)


def struct_path(struct):
    if struct.startswith('/'):
        return struct
    return (RIGVM_UNITS if struct.startswith('RigVMFunction_') else CONTROL_RIG_UNITS) + struct


class GraphBuilder(object):
    def __init__(self, backend):
        self.backend = backend
        self.last_exec = None
        self.version = 0
        self.nodes = []
        self._rows = {}
        self._exec_column = 0

    def begin(self):
        node = self._add('RigUnit_BeginExecution', {}, mutable=False, column=0)
        self.last_exec = node
        return node

    def unit(self, struct, **inputs):
        return self._add(struct, inputs, mutable=False)

    def mutable(self, struct, **inputs):
        node = self._add(struct, inputs, mutable=True)
        self.backend.link_exec(self.last_exec.handle, node.handle)
        self.last_exec = node
        self.version += 1
        return node

    def _add(self, struct, inputs, mutable, column=None):
        if column is None:
            column = 1 + max([p.node.column for p in inputs.values() if isinstance(p, Pin)] or [0])
            if mutable:
                column = max(column, self._exec_column + 1)
                self._exec_column = column
        row = self._rows.get(column, 0)
        self._rows[column] = row + 1
        position = (column * COLUMN_WIDTH, row * ROW_HEIGHT)
        handle, name = self.backend.add_unit(struct_path(struct), position)
        node = Node(struct, handle, name, column, mutable)
        self.nodes.append(node)
        for pin, value in inputs.items():
            if value is None:
                continue
            if isinstance(value, Pin):
                self.backend.link(value.node.handle, value.path, handle, pin)
            else:
                self.backend.set_default(handle, pin, value)
        return node


# ---------------------------------------------------------------------------
# Unreal backend

def format_value(value):
    """Python value -> the text Unreal uses for pin default values."""
    if isinstance(value, bool):
        return 'True' if value else 'False'
    if isinstance(value, (int, float)):
        return '%.6f' % value
    if isinstance(value, Vec):
        return '(X=%.6f,Y=%.6f,Z=%.6f)' % tuple(value)
    if isinstance(value, Quat):
        return '(X=%.8f,Y=%.8f,Z=%.8f,W=%.8f)' % tuple(value)
    if isinstance(value, Xform):
        return '(Rotation=%s,Translation=%s,Scale3D=%s)' % (
            format_value(value.rot), format_value(value.trans), format_value(value.scale))
    if isinstance(value, Key):
        return '(Type=%s,Name="%s")' % (value.type, value.name)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, str):
        return value
    raise TypeError('cannot format %r' % (value,))


def _norm(name):
    return ''.join(c for c in str(name).lower() if c.isalnum())


class GraphError(Exception):
    pass


class UnrealBackend(object):
    """Creates nodes through a RigVMController."""

    def __init__(self, controller):
        import unreal
        self.unreal = unreal
        self.controller = controller
        self._pins = {}

    def add_unit(self, path, position):
        u = self.unreal
        node = self.controller.add_unit_node_from_struct_path(
            path, 'Execute', u.Vector2D(position[0], position[1]), '', False, False)
        if node is None:
            raise GraphError('Could not add a node for %s. Is the Control Rig plugin enabled?' % path)
        name = str(node.get_node_path())
        return node, name

    def _pin_map(self, node):
        name = str(node.get_node_path())
        m = self._pins.get(name)
        if m is None:
            m = {}
            for pin in node.get_pins():
                pin_name = str(pin.get_name())
                k = _norm(pin_name)
                m.setdefault(k, pin_name)
                if k.startswith('b') and pin.get_cpp_type() == 'bool':
                    m.setdefault(k[1:], pin_name)
            self._pins[name] = m
        return m

    def pin_path(self, node, path):
        root, _, rest = path.partition('.')
        m = self._pin_map(node)
        k = _norm(root)
        actual = m.get(k)
        if actual is None and k.startswith('b'):
            actual = m.get(k[1:])
        if actual is None:
            raise GraphError('Node %s (%s) has no pin "%s". Pins: %s' % (
                node.get_node_path(), node.get_node_title(), root, ', '.join(sorted(m.values()))))
        full = '%s.%s' % (node.get_node_path(), actual)
        return full + ('.' + rest if rest else '')

    def set_default(self, node, pin, value):
        path = self.pin_path(node, pin)
        if not self.controller.set_pin_default_value(path, format_value(value), True, False, False, False):
            raise GraphError('Could not set %s to %s' % (path, format_value(value)))

    def link(self, src_node, src_pin, dst_node, dst_pin):
        a = self.pin_path(src_node, src_pin)
        b = self.pin_path(dst_node, dst_pin)
        if not self.controller.add_link(a, b, False, False):
            raise GraphError('Could not link %s -> %s' % (a, b))

    def _exec_pin(self, node, output):
        d = self.unreal.RigVMPinDirection
        wanted = (d.OUTPUT, d.IO) if output else (d.INPUT, d.IO)
        for pin in node.get_pins():
            if pin.is_execute_context() and pin.get_direction() in wanted:
                return pin.get_pin_path()
        raise GraphError('Node %s has no execute pin' % node.get_node_path())

    def link_exec(self, src_node, dst_node):
        a = self._exec_pin(src_node, True)
        b = self._exec_pin(dst_node, False)
        if not self.controller.add_link(a, b, False, False):
            raise GraphError('Could not link execution %s -> %s' % (a, b))
