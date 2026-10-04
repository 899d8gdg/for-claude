"""A stand-in for Unreal's `unreal` Python module, for running the plugin outside the editor.

Every call the plugin makes is checked against the real API stub (method names,
argument names and counts, struct properties, enum values, node struct paths
and pin names). Behind it, the Control Rig is an interp.Rig and the graph an
interp.Evaluator, so a generated rig can be evaluated like the real one.

install() puts the fake in sys.modules['unreal'].
"""

import re
import sys
import types

from stubsig import Stub
from interp import Rig, INode, NODE_SPECS, Evaluator
from car_rig.xmath import Vec as V, Quat as Q, Xform
from car_rig.graph import Key as GKey, Enum as GEnum

STUB = Stub()
calls = []          # (class, method) log
dialogs = []
logs = []


def _chk(cls, method, args, kwargs):
    STUB.check_call(cls, method, args, kwargs)
    calls.append((cls, method))


# ---------------------------------------------------------------------------
# enums

class _EnumValue(object):
    def __init__(self, enum, name):
        self.enum = enum
        self.name = name

    def __eq__(self, other):
        return isinstance(other, _EnumValue) and (other.enum, other.name) == (self.enum, self.name)

    def __hash__(self):
        return hash((self.enum, self.name))

    def __repr__(self):
        return '%s.%s' % (self.enum, self.name)


class _Enum(object):
    def __init__(self, name):
        self._name = name

    def __getattr__(self, value):
        if value.startswith('_'):
            raise AttributeError(value)
        STUB.check_enum(self._name, value)
        return _EnumValue(self._name, value)


ENUMS = ('RigElementType', 'RigControlType', 'RigControlAnimationType', 'RigControlAxis',
         'RigControlValueType', 'RigVMPinDirection', 'AppMsgType', 'AppReturnType', 'MultiBoxType',
         'MovieSceneTimeUnit')


# ---------------------------------------------------------------------------
# structs

class _Struct(object):
    _ue = None

    def __setattr__(self, k, v):
        if not k.startswith('_') and self._ue and not STUB.has_property(self._ue, k):
            raise AttributeError('unreal.%s has no property %s' % (self._ue, k))
        object.__setattr__(self, k, v)


class Vector(_Struct):
    _ue = 'Vector'

    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = float(x), float(y), float(z)


class Vector2D(_Struct):
    _ue = 'Vector2D'

    def __init__(self, x=0.0, y=0.0):
        self.x, self.y = float(x), float(y)


class Quat(_Struct):
    _ue = 'Quat'

    def __init__(self, x=0.0, y=0.0, z=0.0, w=1.0):
        self.x, self.y, self.z, self.w = float(x), float(y), float(z), float(w)


class Rotator(_Struct):
    _ue = 'Rotator'

    def __init__(self, roll=0.0, pitch=0.0, yaw=0.0):
        self.roll, self.pitch, self.yaw = roll, pitch, yaw


class EulerTransform(_Struct):
    _ue = 'EulerTransform'


class LinearColor(_Struct):
    _ue = 'LinearColor'

    def __init__(self, r=0.0, g=0.0, b=0.0, a=0.0):
        self.r, self.g, self.b, self.a = r, g, b, a


class Transform(_Struct):
    _ue = 'Transform'

    def __init__(self, location=None, rotation=None, scale=None):
        assert rotation is None or isinstance(rotation, Rotator)
        self.translation = location or Vector()
        self.rotation = Quat()
        self.scale3d = scale or Vector(1, 1, 1)


def _to_x(t):
    q, p, s = t.rotation, t.translation, t.scale3d
    return Xform(Q(q.x, q.y, q.z, q.w), V(p.x, p.y, p.z), V(s.x, s.y, s.z))


def _from_x(xf):
    t = Transform()
    t.translation = Vector(*xf.trans)
    t.rotation = Quat(*xf.rot)
    t.scale3d = Vector(*xf.scale)
    return t


class RigElementKey(_Struct):
    _ue = 'RigElementKey'

    def __init__(self, type=None, name='None'):
        self.type = type if type is not None else _Enum('RigElementType').NONE
        self.name = name


class RigControlValue(_Struct):
    _ue = 'RigControlValue'

    def __init__(self, value=None):
        object.__setattr__(self, '_value', value)


class RigControlLimitEnabled(_Struct):
    _ue = 'RigControlLimitEnabled'

    def __init__(self, minimum=False, maximum=False):
        self.minimum, self.maximum = minimum, maximum


class RigControlSettings(_Struct):
    _ue = 'RigControlSettings'

    def __init__(self):
        self.control_type = _Enum('RigControlType').BOOL
        self.animation_type = _Enum('RigControlAnimationType').ANIMATION_CONTROL
        self.display_name = 'None'


class FrameNumber(_Struct):
    _ue = 'FrameNumber'

    def __init__(self, value=0):
        self.value = int(value)


class BoxSphereBounds(_Struct):
    _ue = 'BoxSphereBounds'

    def __init__(self, origin, box_extent):
        self.origin, self.box_extent = origin, box_extent


# ---------------------------------------------------------------------------
# objects

class Object(object):
    _ue = 'Object'

    def __init__(self, name='Object', path=None):
        self._name = name
        self._path = path or '/Game/%s.%s' % (name, name)

    def get_name(self):
        _chk('Object', 'get_name', (), {})
        return self._name

    def get_path_name(self):
        _chk('Object', 'get_path_name', (), {})
        return self._path


class Skeleton(Object):
    pass


class SkeletalMesh(Object):
    """Test mesh: bones {name: Xform}, parents {name: parent}."""

    def __init__(self, name, folder, bones, parents, bounds=None):
        Object.__init__(self, name, '%s/%s.%s' % (folder, name, name))
        self.bones = bones
        self.parents = parents
        self._bounds = bounds
        self.skeleton = Skeleton(name + '_Skeleton')

    def get_bounds(self):
        _chk('SkeletalMesh', 'get_bounds', (), {})
        if self._bounds is None:
            return BoxSphereBounds(Vector(), Vector())
        lo, hi = self._bounds
        o = (lo + hi) * 0.5
        e = (hi - lo) * 0.5
        return BoxSphereBounds(Vector(*o), Vector(*e))

    def get_imported_bounds(self):
        _chk('SkeletalMesh', 'get_imported_bounds', (), {})
        return self.get_bounds()


_KIND = {'BONE': 'Bone', 'NULL': 'Null', 'CONTROL': 'Control'}
_TYPE = {v: k for k, v in _KIND.items()}


def _kind(key):
    return _KIND[key.type.name]


def _key(kind, name):
    return RigElementKey(_Enum('RigElementType').__getattr__(_TYPE[kind]), name)


class RigHierarchy(Object):
    def __init__(self, rig, channels):
        Object.__init__(self, 'RigHierarchy')
        self.rig = rig
        self.channels = channels      # element name -> dict(host, display, value)

    def _keys(self, kind):
        return [_key(kind, e.name) for e in self.rig.order if e.kind == kind]

    def get_controls(self, traverse=True):
        _chk('RigHierarchy', 'get_controls', (traverse,), {})
        return self._keys('Control') + [_key('Control', n) for n in self.channels]

    def get_nulls(self, traverse=True):
        _chk('RigHierarchy', 'get_nulls', (traverse,), {})
        return self._keys('Null')

    def get_bones(self, traverse=True):
        _chk('RigHierarchy', 'get_bones', (traverse,), {})
        return self._keys('Bone')

    def contains(self, key):
        _chk('RigHierarchy', 'contains', (key,), {})
        if _kind(key) == 'Control' and key.name in self.channels:
            return True
        return (_kind(key), key.name) in self.rig.elements

    def get_global_transform(self, key, initial=False):
        _chk('RigHierarchy', 'get_global_transform', (key, initial), {})
        return _from_x(self.rig.global_(self.rig.find((_kind(key), key.name)), initial))

    def set_control_offset_transform(self, *args, **kw):
        _chk('RigHierarchy', 'set_control_offset_transform', args, kw)
        key, transform = args[0], args[1]
        e = self.rig.find(('Control', key.name))
        e.offset = _to_x(transform)

    def set_control_shape_transform(self, *args, **kw):
        _chk('RigHierarchy', 'set_control_shape_transform', args, kw)

    def set_control_value(self, *args, **kw):
        _chk('RigHierarchy', 'set_control_value', args, kw)
        key, value = args[0], args[1]
        if key.name in self.channels:
            self.channels[key.name]['value'] = value._value

    @classmethod
    def _make(cls, method, value):
        _chk('RigHierarchy', method, (value,), {})
        return RigControlValue(value)

    @classmethod
    def make_control_value_from_float(cls, value):
        return cls._make('make_control_value_from_float', value)

    @classmethod
    def make_control_value_from_vector(cls, value):
        return cls._make('make_control_value_from_vector', value)

    @classmethod
    def make_control_value_from_rotator(cls, value):
        return cls._make('make_control_value_from_rotator', value)

    @classmethod
    def make_control_value_from_euler_transform(cls, value):
        return cls._make('make_control_value_from_euler_transform', value)


class RigHierarchyController(Object):
    def __init__(self, hierarchy):
        Object.__init__(self, 'RigHierarchyController')
        self.h = hierarchy

    def _parent(self, key):
        if key.type.name == 'NONE':
            return None
        kind = _kind(key)
        assert (kind, key.name) in self.h.rig.elements, 'parent %s does not exist' % key.name
        return (kind, key.name)

    def remove_element(self, *args, **kw):
        _chk('RigHierarchyController', 'remove_element', args, kw)
        key = args[0]
        if key.name in self.h.channels:
            del self.h.channels[key.name]
            return True
        e = self.h.rig.elements.pop((_kind(key), key.name))
        self.h.rig.order.remove(e)
        return True

    def import_bones_from_skeletal_mesh(self, *args, **kw):
        _chk('RigHierarchyController', 'import_bones_from_skeletal_mesh', args, kw)
        mesh = args[0]
        rig = self.h.rig
        for name in mesh.bones:
            if ('Bone', name) in rig.elements:
                continue
            parent = mesh.parents.get(name)
            rig.add_global('Bone', name, ('Bone', parent) if parent else None, mesh.bones[name])
        return [_key('Bone', n) for n in mesh.bones]

    def add_null(self, *args, **kw):
        _chk('RigHierarchyController', 'add_null', args, kw)
        name, parent, transform = args[0], args[1], args[2]
        in_global = args[3] if len(args) > 3 else kw.get('transform_in_global', True)
        assert in_global
        self.h.rig.add_global('Null', name, self._parent(parent), _to_x(transform))
        return _key('Null', name)

    def add_control(self, *args, **kw):
        _chk('RigHierarchyController', 'add_control', args, kw)
        name, parent, settings, value = args[:4]
        assert isinstance(settings, RigControlSettings) and isinstance(value, RigControlValue)
        e = self.h.rig.add('Control', name, self._parent(parent))
        e.offset = Xform()
        e.settings = settings
        return _key('Control', name)

    def add_animation_channel(self, *args, **kw):
        _chk('RigHierarchyController', 'add_animation_channel', args, kw)
        name, host, settings = args[:3]
        assert settings.animation_type.name == 'ANIMATION_CHANNEL'
        assert ('Control', host.name) in self.h.rig.elements
        self.h.channels[name] = {'host': host.name, 'display': settings.display_name, 'value': 0.0}
        return _key('Control', name)


# --- graph -----------------------------------------------------------------

def parse_value(text):
    text = text.strip()
    if text in ('True', 'False'):
        return text == 'True'
    try:
        return float(text)
    except ValueError:
        pass
    if text.startswith('(') and text.endswith(')'):
        body = text[1:-1]
        parts, depth, cur = [], 0, ''
        for ch in body:
            depth += ch in '(['
            depth -= ch in ')]'
            if ch == ',' and depth == 0:
                parts.append(cur)
                cur = ''
            else:
                cur += ch
        parts.append(cur)
        d = {}
        for p in parts:
            k, _, v = p.partition('=')
            v = v.strip()
            d[k.strip()] = v[1:-1] if v.startswith('"') else parse_value(v)
        keys = set(d)
        if keys == {'X', 'Y', 'Z'}:
            return V(d['X'], d['Y'], d['Z'])
        if keys == {'X', 'Y', 'Z', 'W'}:
            return Q(d['X'], d['Y'], d['Z'], d['W'])
        if keys == {'Rotation', 'Translation', 'Scale3D'}:
            return Xform(d['Rotation'], d['Translation'], d['Scale3D'])
        if keys == {'Type', 'Name'}:
            return GKey(d['Type'], d['Name'])
        raise ValueError('unexpected struct text %r' % text)
    if text in ('GlobalSpace', 'LocalSpace'):
        return GEnum(text)
    return text


EXEC = 'ExecutePin'
BOOL_PINS = {'bInitial', 'bPropagateToChildren', 'bHit', 'bClamp'}


class RigVMPin(Object):
    def __init__(self, node, name, direction, is_exec=False):
        Object.__init__(self, name)
        self.node, self.direction, self.is_exec = node, direction, is_exec

    def get_cpp_type(self):
        _chk('RigVMPin', 'get_cpp_type', (), {})
        return 'bool' if self._name in BOOL_PINS else 'double'

    def is_execute_context(self):
        _chk('RigVMPin', 'is_execute_context', (), {})
        return self.is_exec

    def get_direction(self):
        _chk('RigVMPin', 'get_direction', (), {})
        return _Enum('RigVMPinDirection').__getattr__(self.direction)

    def get_pin_path(self, use_node_path=False):
        _chk('RigVMPin', 'get_pin_path', (use_node_path,), {})
        return '%s.%s' % (self.node.inode.name, self._name)


class RigVMNode(Object):
    def __init__(self, inode, mutable, event):
        Object.__init__(self, inode.name)
        self.inode = inode
        ins, outs = NODE_SPECS[inode.struct]
        self.pins = [RigVMPin(self, p, 'INPUT') for p in ins] + [RigVMPin(self, p, 'OUTPUT') for p in outs]
        if event:
            self.pins.append(RigVMPin(self, EXEC, 'OUTPUT', True))
        elif mutable:
            self.pins.append(RigVMPin(self, EXEC, 'IO', True))

    def get_pins(self):
        _chk('RigVMNode', 'get_pins', (), {})
        return list(self.pins)

    def get_node_path(self, recursive=False):
        _chk('RigVMNode', 'get_node_path', (recursive,), {})
        return self.inode.name

    def get_node_title(self):
        _chk('RigVMNode', 'get_node_title', (), {})
        return self.inode.struct

    def pin(self, name):
        for p in self.pins:
            if p._name == name:
                return p
        raise KeyError('%s has no pin %s' % (self.inode.name, name))


class RigVMGraph(Object):
    def __init__(self, controller):
        Object.__init__(self, 'RigVMModel')
        self.c = controller

    def get_nodes(self):
        _chk('RigVMGraph', 'get_nodes', (), {})
        return list(self.c.nodes.values())


MUTABLE = {'RigUnit_SetTransform'}


class RigVMController(Object):
    def __init__(self):
        Object.__init__(self, 'RigVMController')
        self.nodes = {}
        self.begin = None
        self.graph = RigVMGraph(self)
        self.default_nodes = ['RigUnit_BeginExecution']
        for struct in self.default_nodes:
            self._new(struct)

    def _new(self, struct, name=None):
        short = struct.replace('RigUnit_', '').replace('RigVMFunction_', '')
        inode = INode(struct, name or '%s_%d' % (short, len(self.nodes)))
        node = RigVMNode(inode, struct in MUTABLE, struct == 'RigUnit_BeginExecution')
        self.nodes[inode.name] = node
        if struct == 'RigUnit_BeginExecution':
            self.begin = inode
        return node

    def get_graph(self):
        _chk('RigVMController', 'get_graph', (), {})
        return self.graph

    def remove_nodes_by_name(self, *args, **kw):
        _chk('RigVMController', 'remove_nodes_by_name', args, kw)
        for name in args[0]:
            node = self.nodes.pop(name)
            if node.inode is self.begin:
                self.begin = None
        return True

    def add_unit_node_from_struct_path(self, *args, **kw):
        _chk('RigVMController', 'add_unit_node_from_struct_path', args, kw)
        path = args[0]
        m = re.match(r'^/Script/(\w+)\.(\w+)$', path)
        assert m, path
        module, struct = m.groups()
        cls = STUB.classes.get(struct)
        assert cls is not None, 'no struct %s in Unreal' % struct
        assert cls.module == module, '%s lives in /Script/%s, not /Script/%s' % (struct, cls.module, module)
        assert args[1] == 'Execute'
        # every pin the graph uses must exist on the real struct
        ins, outs = NODE_SPECS[struct]
        for pin in ins + outs:
            py = re.sub(r'(?<!^)(?=[A-Z])', '_', pin[1:] if pin in BOOL_PINS else pin).lower()
            py = py.replace('scale3_d', 'scale3d')
            if py in ('global', 'local'):
                py_alt = py + '_'
            else:
                py_alt = py
            assert STUB.has_property(struct, py) or STUB.has_property(struct, py_alt), \
                'unreal.%s has no pin %s (%s)' % (struct, pin, py)
        if struct == 'RigUnit_BeginExecution' and self.begin is not None:
            return None
        return self._new(struct)

    def _split(self, path):
        node, _, pin = path.partition('.')
        return self.nodes[node], pin

    def set_pin_default_value(self, *args, **kw):
        _chk('RigVMController', 'set_pin_default_value', args, kw)
        node, pin = self._split(args[0])
        assert node.pin(pin.split('.')[0]).direction == 'INPUT', args[0]
        node.inode.defaults[pin] = parse_value(args[1])
        return True

    def add_link(self, *args, **kw):
        _chk('RigVMController', 'add_link', args, kw)
        a, pa = self._split(args[0])
        b, pb = self._split(args[1])
        src, dst = a.pin(pa.split('.')[0]), b.pin(pb.split('.')[0])
        if src.is_exec:
            assert dst.is_exec and dst.direction in ('INPUT', 'IO') and src.direction in ('OUTPUT', 'IO')
            assert a.inode.exec_next is None
            a.inode.exec_next = b.inode
            return True
        assert src.direction == 'OUTPUT' and dst.direction == 'INPUT', (args[0], args[1])
        assert pb not in b.inode.links, 'input linked twice: ' + args[1]
        b.inode.links[pb] = (a.inode, pa)
        return True


class ControlRigBlueprint(Object):
    def __init__(self, path):
        Object.__init__(self, path.rsplit('/', 1)[1], '%s.%s' % (path, path.rsplit('/', 1)[1]))
        self.rig = Rig()
        self.channels = {}
        self._hierarchy = RigHierarchy(self.rig, self.channels)
        self.hc = RigHierarchyController(self._hierarchy)
        self.controller = RigVMController()
        self.preview = None
        self.compiled = 0

    @property
    def hierarchy(self):
        return self._hierarchy

    def get_hierarchy_controller(self):
        _chk('ControlRigBlueprint', 'get_hierarchy_controller', (), {})
        return self.hc

    def get_controller_by_name(self, *args, **kw):
        _chk('ControlRigBlueprint', 'get_controller_by_name', args, kw)
        return self.controller if args[0] == 'RigVMModel' else None

    def get_controller(self, *args, **kw):
        _chk('ControlRigBlueprint', 'get_controller', args, kw)
        return self.controller

    def set_preview_mesh(self, *args, **kw):
        _chk('ControlRigBlueprint', 'set_preview_mesh', args, kw)
        self.preview = args[0]

    def set_auto_vm_recompile(self, *args, **kw):
        _chk('ControlRigBlueprint', 'set_auto_vm_recompile', args, kw)

    def recompile_vm(self):
        _chk('ControlRigBlueprint', 'recompile_vm', (), {})
        self.compiled += 1

    # test helpers
    def evaluator(self):
        defaults = {(c['host'], c['display']): c['value'] for c in self.channels.values()}
        backend = types.SimpleNamespace(begin=self.controller.begin)
        return Evaluator(backend, self.rig, defaults)


class ControlRigBlueprintFactory(Object):
    @classmethod
    def create_new_control_rig_asset(cls, *args, **kw):
        _chk('ControlRigBlueprintFactory', 'create_new_control_rig_asset', args, kw)
        path = kw.get('desired_package_path', args[0] if args else None)
        bp = ControlRigBlueprint(path)
        ASSETS[path] = bp
        return bp


ASSETS = {}


class EditorAssetLibrary(object):
    @classmethod
    def does_asset_exist(cls, *args, **kw):
        _chk('EditorAssetLibrary', 'does_asset_exist', args, kw)
        return args[0] in ASSETS

    @classmethod
    def load_asset(cls, *args, **kw):
        _chk('EditorAssetLibrary', 'load_asset', args, kw)
        return ASSETS.get(args[0])

    @classmethod
    def save_loaded_asset(cls, *args, **kw):
        _chk('EditorAssetLibrary', 'save_loaded_asset', args, kw)
        return True


class ScopedSlowTask(object):
    def __init__(self, work, desc='', enabled=True):
        self.cancel = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def make_dialog(self, can_cancel=False, allow_in_pie=False):
        pass

    def enter_progress_frame(self, work=1.0, desc=''):
        pass

    def should_cancel(self):
        return self.cancel


class EditorDialog(object):
    @classmethod
    def show_message(cls, *args, **kw):
        _chk('EditorDialog', 'show_message', args, kw)
        dialogs.append(args[1])


SELECTED = []


class EditorUtilityLibrary(object):
    @classmethod
    def get_selected_assets(cls):
        _chk('EditorUtilityLibrary', 'get_selected_assets', (), {})
        return list(SELECTED)


# --- menus -----------------------------------------------------------------

class ToolMenuEntryScript(Object):
    def __init__(self):
        Object.__init__(self, type(self).__name__)

    def init_entry(self, *args, **kw):
        _chk('ToolMenuEntryScript', 'init_entry', args, kw)
        self.menu_name, self.label = args[1], args[4]


class ToolMenu(Object):
    def __init__(self, name):
        Object.__init__(self, name)
        self.entries = []
        self.subs = {}

    def add_sub_menu(self, *args, **kw):
        _chk('ToolMenu', 'add_sub_menu', args, kw)
        sub = MENUS.extend_menu('%s.%s' % (self._name, args[2]))
        self.subs[args[2]] = sub
        return sub

    def add_menu_entry_object(self, *args, **kw):
        _chk('ToolMenu', 'add_menu_entry_object', args, kw)
        assert isinstance(args[0], ToolMenuEntryScript)
        assert args[0].menu_name == self._name, (args[0].menu_name, self._name)
        self.entries.append(args[0])


class ToolMenus(Object):
    def __init__(self):
        Object.__init__(self, 'ToolMenus')
        self.menus = {}

    @classmethod
    def get(cls):
        _chk('ToolMenus', 'get', (), {})
        return MENUS

    def extend_menu(self, *args, **kw):
        _chk('ToolMenus', 'extend_menu', args, kw)
        return self.menus.setdefault(args[0], ToolMenu(args[0]))

    def refresh_all_widgets(self):
        _chk('ToolMenus', 'refresh_all_widgets', (), {})


MENUS = ToolMenus()


def uclass(*a, **k):
    return lambda cls: cls


def ufunction(*a, **k):
    assert k.get('override') is True
    return lambda fn: fn


def log(msg):
    logs.append(('log', msg))


def log_warning(msg):
    logs.append(('warning', msg))


def log_error(msg):
    logs.append(('error', msg))


# --- sequencer ---------------------------------------------------------------

class LevelSequence(Object):
    """A sequence that animates control values: animate(frame) -> {control: Xform value}."""

    def __init__(self, start, end, animate):
        Object.__init__(self, 'Seq')
        self.start, self.end, self.animate = start, end, animate
        self.keys = {}        # control name -> {frame: value}

    def get_playback_start(self):
        _chk('MovieSceneSequence', 'get_playback_start', (), {})
        return self.start

    def get_playback_end(self):
        _chk('MovieSceneSequence', 'get_playback_end', (), {})
        return self.end


class LevelSequenceEditorBlueprintLibrary(object):
    current = None

    @classmethod
    def get_current_level_sequence(cls):
        _chk('LevelSequenceEditorBlueprintLibrary', 'get_current_level_sequence', (), {})
        return cls.current


class ControlRig(Object):
    def __init__(self, bp):
        Object.__init__(self, 'CR_Instance')
        self.bp = bp

    def get_hierarchy(self):
        _chk('ControlRig', 'get_hierarchy', (), {})
        return self.bp.hierarchy


class _Key(object):
    def __init__(self, frame):
        self.frame = frame


class _Channel(object):
    def __init__(self, seq, name):
        self.seq, self.channel_name = seq, name

    def get_keys(self):
        _chk('MovieSceneScriptingFloatChannel', 'get_keys', (), {})
        return [_Key(f) for f in sorted(self.seq.keys.get(self.channel_name, {}))]

    def remove_key(self, *args, **kw):
        _chk('MovieSceneScriptingFloatChannel', 'remove_key', args, kw)
        del self.seq.keys[self.channel_name][args[0].frame]


class _Section(object):
    def __init__(self, seq, names):
        self.seq, self.names = seq, names

    def get_all_channels(self):
        _chk('MovieSceneSection', 'get_all_channels', (), {})
        return [_Channel(self.seq, n) for n in self.names]


class _Track(object):
    def __init__(self, seq, names):
        self.section = _Section(seq, names)

    def get_sections(self):
        _chk('MovieSceneTrack', 'get_sections', (), {})
        return [self.section]


class ControlRigSequencerBindingProxy(object):
    def __init__(self, control_rig, track):
        self.control_rig, self.track = control_rig, track


class ControlRigSequencerLibrary(object):
    proxies = []

    @classmethod
    def get_control_rigs(cls, *args, **kw):
        _chk('ControlRigSequencerLibrary', 'get_control_rigs', args, kw)
        return list(cls.proxies)

    @classmethod
    def _evaluate(cls, seq, rig, frame):
        bp = rig.bp
        bp.rig.reset()
        for name, value in seq.animate(frame).items():
            bp.rig.find(('Control', name)).value = value
        ev = bp.evaluator()
        for name, keys in seq.keys.items():
            ch = bp.channels.get(name)
            if ch and keys:
                ev.channel_defaults[(ch['host'], ch['display'])] = _sample(keys, frame)
        ev.run()

    @classmethod
    def get_control_rig_world_transforms(cls, *args, **kw):
        _chk('ControlRigSequencerLibrary', 'get_control_rig_world_transforms', args, kw)
        seq, rig, name, frames = args[:4]
        out = []
        for f in frames:
            cls._evaluate(seq, rig, f.value)
            out.append(_from_x(rig.bp.rig.global_(rig.bp.rig.find(('Control', name)))))
        return out

    @classmethod
    def get_local_control_rig_float(cls, *args, **kw):
        _chk('ControlRigSequencerLibrary', 'get_local_control_rig_float', args, kw)
        seq, rig, name, frame = args[:4]
        keys = seq.keys.get(name)
        if keys:
            return _sample(keys, frame.value)
        ch = rig.bp.channels.get(name)
        return ch['value'] if ch else 0.0

    @classmethod
    def set_local_control_rig_float(cls, *args, **kw):
        _chk('ControlRigSequencerLibrary', 'set_local_control_rig_float', args, kw)
        seq, rig, name, frame, value = args[:5]
        assert name in rig.bp.channels or ('Control', name) in rig.bp.rig.elements, name
        seq.keys.setdefault(name, {})[frame.value] = value


def _sample(keys, frame):
    frames = sorted(keys)
    if frame <= frames[0]:
        return keys[frames[0]]
    if frame >= frames[-1]:
        return keys[frames[-1]]
    for a, b in zip(frames, frames[1:]):
        if a <= frame <= b:
            t = (frame - a) / float(b - a)
            return keys[a] + (keys[b] - keys[a]) * t


def install():
    mod = types.ModuleType('unreal')
    g = dict(globals())
    for name, value in g.items():
        if not name.startswith('_') and name not in ('re', 'sys', 'types', 'install'):
            setattr(mod, name, value)
    for e in ENUMS:
        setattr(mod, e, _Enum(e))
    sys.modules['unreal'] = mod
    return mod
