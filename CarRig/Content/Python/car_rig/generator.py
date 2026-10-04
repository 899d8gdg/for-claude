"""Creates the car Control Rig asset for a skeletal mesh (runs inside the Unreal Editor)."""

import unreal

from .graph import GraphBuilder, UnrealBackend
from .layout import build_layout, LayoutError
from .solve import build_forward_solve
from .xmath import Vec, Quat, Xform, make_relative

MODEL_NAME = 'RigVMModel'


class GenerateError(Exception):
    pass


# ---------------------------------------------------------------------------
# conversions

def to_ue(xf):
    t = unreal.Transform()
    t.translation = unreal.Vector(*xf.trans)
    t.rotation = unreal.Quat(*xf.rot)
    t.scale3d = unreal.Vector(*xf.scale)
    return t


def from_ue(t):
    q = t.rotation
    p = t.translation
    s = t.scale3d
    return Xform(Quat(q.x, q.y, q.z, q.w), Vec(p.x, p.y, p.z), Vec(s.x, s.y, s.z))


def _control_value(control_type, default):
    h = unreal.RigHierarchy
    if control_type == 'EULER_TRANSFORM':
        return h.make_control_value_from_euler_transform(unreal.EulerTransform())
    if control_type == 'ROTATOR':
        return h.make_control_value_from_rotator(unreal.Rotator())
    if control_type == 'POSITION':
        return h.make_control_value_from_vector(unreal.Vector())
    if control_type == 'FLOAT':
        return h.make_control_value_from_float(float(default or 0.0))
    raise GenerateError('unsupported control type %s' % control_type)


def _settings(control_type, display_name=None, shape=None, color=None, minimum=None, maximum=None,
              primary_axis='X', channel=False):
    s = unreal.RigControlSettings()
    s.animation_type = (unreal.RigControlAnimationType.ANIMATION_CHANNEL if channel
                        else unreal.RigControlAnimationType.ANIMATION_CONTROL)
    s.control_type = getattr(unreal.RigControlType, control_type)
    if display_name:
        s.display_name = display_name
    if shape:
        s.shape_name = shape
        s.shape_visible = True
    if color:
        s.shape_color = unreal.LinearColor(color[0], color[1], color[2], 1.0)
    s.primary_axis = getattr(unreal.RigControlAxis, primary_axis)
    if minimum is not None and maximum is not None:
        h = unreal.RigHierarchy
        s.limit_enabled = [unreal.RigControlLimitEnabled(True, True)]
        s.minimum_value = h.make_control_value_from_float(float(minimum))
        s.maximum_value = h.make_control_value_from_float(float(maximum))
        s.draw_limits = True
    return s


# ---------------------------------------------------------------------------

def control_rig_path(mesh):
    package = mesh.get_path_name().split('.')[0]          # /Game/Cars/SK_Car
    folder = package.rsplit('/', 1)[0]
    name = mesh.get_name()
    if name.startswith('SK_'):
        name = name[3:]
    return '%s/CR_%s' % (folder, name)


def _load_or_create(path):
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        bp = unreal.EditorAssetLibrary.load_asset(path)
        if not isinstance(bp, unreal.ControlRigBlueprint):
            raise GenerateError('%s already exists and is not a Control Rig. Rename or delete it first.' % path)
        return bp, False
    bp = unreal.ControlRigBlueprintFactory.create_new_control_rig_asset(desired_package_path=path)
    if bp is None:
        raise GenerateError('Could not create a Control Rig at %s.' % path)
    return bp, True


def _import_bones(bp, mesh):
    hc = bp.get_hierarchy_controller()
    hierarchy = bp.hierarchy
    # Start from a clean hierarchy: remove anything a previous Generate made.
    old = list(hierarchy.get_controls()) + list(hierarchy.get_nulls())
    for key in reversed(old):
        if hierarchy.contains(key):
            hc.remove_element(key, False, False)
    try:
        hc.import_bones_from_skeletal_mesh(mesh, 'None', True, True, False, False, False)
    except AttributeError:
        hc.import_bones(mesh.skeleton, 'None', True, True, False, False, False)
    bones = {}
    for key in hierarchy.get_bones():
        bones[str(key.name)] = from_ue(hierarchy.get_global_transform(key, True))
    if not bones:
        raise GenerateError('%s has no bones.' % mesh.get_name())
    return bones


def _bounds(mesh):
    try:
        b = mesh.get_imported_bounds()
    except Exception:
        b = mesh.get_bounds()
    o, e = b.origin, b.box_extent
    if e.x <= 0.0 and e.y <= 0.0 and e.z <= 0.0:
        return None
    return Vec(o.x - e.x, o.y - e.y, o.z - e.z), Vec(o.x + e.x, o.y + e.y, o.z + e.z)


def _create_elements(bp, lay):
    hc = bp.get_hierarchy_controller()
    hierarchy = bp.hierarchy
    keys = {}
    rest = {}
    for e in lay.elements:
        parent = keys[e.parent] if e.parent else unreal.RigElementKey()
        parent_rest = rest[e.parent] if e.parent else Xform()
        if e.kind == 'null':
            key = hc.add_null(e.name, parent, to_ue(e.xform), True, False, False)
        else:
            settings = _settings(e.control_type, shape=e.shape, color=e.color, minimum=e.minimum,
                                 maximum=e.maximum, primary_axis=e.primary_axis)
            key = hc.add_control(e.name, parent, settings, _control_value(e.control_type, e.default),
                                 False, False)
            offset = to_ue(make_relative(e.xform, parent_rest))
            shape = to_ue(e.shape_xform)
            for initial in (True, False):
                hierarchy.set_control_offset_transform(key, offset, initial, True, False, False)
                hierarchy.set_control_shape_transform(key, shape, initial, False)
        if key is None or str(key.name) != e.name:
            raise GenerateError('Could not create %s (got %s).' % (e.name, key and key.name))
        keys[e.name] = key
        rest[e.name] = e.xform

    for ch in lay.channels:
        settings = _settings('FLOAT', display_name=ch.name, minimum=ch.minimum, maximum=ch.maximum,
                             channel=True)
        key = hc.add_animation_channel(ch.name, keys[ch.host], settings, False, False)
        if key is None:
            raise GenerateError('Could not add animation channel %s to %s.' % (ch.name, ch.host))
        value = unreal.RigHierarchy.make_control_value_from_float(float(ch.default))
        for value_type in (unreal.RigControlValueType.INITIAL, unreal.RigControlValueType.CURRENT):
            hierarchy.set_control_value(key, value, value_type, False, False)
    return keys


def _build_graph(bp, lay):
    controller = bp.get_controller_by_name(MODEL_NAME) or bp.get_controller()
    if controller is None:
        raise GenerateError('The Control Rig has no graph to edit.')
    graph = controller.get_graph()
    names = [n.get_name() for n in graph.get_nodes()]
    if names:
        controller.remove_nodes_by_name(names, False, False)
    builder = GraphBuilder(UnrealBackend(controller))
    build_forward_solve(builder, lay)
    return len(builder.nodes)


def generate(mesh):
    """Build (or rebuild) the car Control Rig for `mesh`. Returns (blueprint, layout, node count)."""
    if not isinstance(mesh, unreal.SkeletalMesh):
        raise GenerateError('Select a Skeletal Mesh.')
    path = control_rig_path(mesh)
    bp, created = _load_or_create(path)
    with unreal.ScopedSlowTask(4, 'Generating car rig') as task:
        task.make_dialog(False)
        bp.set_auto_vm_recompile(False)
        try:
            task.enter_progress_frame(1, 'Importing bones')
            bp.set_preview_mesh(mesh, True)
            bones = _import_bones(bp, mesh)
            try:
                lay = build_layout(bones, _bounds(mesh))
            except LayoutError as e:
                raise GenerateError(str(e))
            task.enter_progress_frame(1, 'Adding controls')
            _create_elements(bp, lay)
            task.enter_progress_frame(1, 'Building the Forward Solve graph')
            count = _build_graph(bp, lay)
        finally:
            bp.set_auto_vm_recompile(True)
        task.enter_progress_frame(1, 'Compiling')
        bp.recompile_vm()
    unreal.EditorAssetLibrary.save_loaded_asset(bp, False)
    for w in lay.warnings:
        unreal.log_warning('Car Rig: ' + w)
    unreal.log('Car Rig: %s %s with %d wheels and %d graph nodes.' % (
        'created' if created else 'rebuilt', path, len(lay.wheels), count))
    return bp, lay, count
