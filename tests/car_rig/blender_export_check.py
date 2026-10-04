"""End-to-end check of the Blender side, run with Blender's Python (bpy module):

    python blender_export_check.py /path/to/rigacar /path/to/out.glb

Builds a toy car, rigs it with Rigacar's "car deformation rig", exports it with
rigacar_to_unreal.py, then reads the .glb back and runs the Unreal rig layout
and solve on its bones.
"""

import json
import math
import os
import struct
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..', '..')
sys.path.insert(0, os.path.join(ROOT, 'CarRig', 'Blender'))
sys.path.insert(0, HERE)

# bpy as a module can hang on exit, so main() ends with os._exit
rigacar_parent, out_path = sys.argv[1], sys.argv[2]
sys.path.insert(0, rigacar_parent)

import rigacar  # noqa: E402
import rigacar_to_unreal  # noqa: E402


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    # Background Blender skips invoke(), where Rigacar reads the selected parts.
    # Run it from execute() the way the UI would.
    import rigacar.car_rig as car_rig
    op = car_rig.OBJECT_OT_armatureCarDeformationRig
    run_execute, run_invoke = op.execute, op.invoke

    def execute(self, context):
        try:
            self.bones_position
        except AttributeError:
            return run_invoke(self, context, None)
        return run_execute(self, context)
    op.execute = execute
    rigacar.register()
    rigacar_to_unreal.register()

    R = 0.35

    def add(kind, name, loc, **kw):
        if kind == 'cube':
            bpy.ops.mesh.primitive_cube_add(location=loc, **kw)
        else:
            bpy.ops.mesh.primitive_cylinder_add(radius=R, depth=0.25, location=loc,
                                                rotation=(0.0, math.pi / 2, 0.0))
        ob = bpy.context.active_object
        ob.name = name
        return ob

    # Rigacar convention: the car faces -Y, left wheels at +X, standing on Z = 0
    parts = [
        add('cube', 'Body', (0.0, 0.0, 0.8), scale=(0.9, 2.2, 0.45)),
        add('cyl', 'Wheel.Ft.L', (0.9, -1.4, R)),
        add('cyl', 'Wheel.Ft.R', (-0.9, -1.4, R)),
        add('cyl', 'Wheel.Bk.L', (0.9, 1.4, R)),
        add('cyl', 'Wheel.Bk.R', (-0.9, 1.4, R)),
    ]
    door = add('cube', 'Door', (0.95, -0.2, 0.8), scale=(0.05, 0.5, 0.3))
    door.parent = parts[0]
    door.matrix_parent_inverse = parts[0].matrix_world.inverted()

    for ob in bpy.context.view_layer.objects:
        ob.select_set(ob in parts)
    bpy.context.view_layer.objects.active = parts[0]
    assert bpy.ops.object.armature_car_deformation_rig() == {'FINISHED'}
    arm = bpy.context.active_object
    assert arm.type == 'ARMATURE', arm
    bones = sorted(b.name for b in arm.data.bones)
    print('Rigacar bones:', bones)
    for ob in parts:
        assert ob.parent is arm and ob.parent_type == 'BONE', (ob.name, ob.parent, ob.parent_type)

    # pose the rig, to check the export uses the rest pose
    arm.pose.bones['DEF-Wheel.Ft.L'].rotation_quaternion = (0.9, 0.0, 0.0, 0.43)

    before = sorted(o.name for o in bpy.data.objects)
    count, skipped = rigacar_to_unreal.export_car(bpy.context, arm, out_path)
    after = sorted(o.name for o in bpy.data.objects)
    assert before == after, 'scene changed: %s' % (set(after) ^ set(before))
    assert all(ob.parent_type == 'BONE' for ob in parts), 'originals were re-parented'
    assert arm.data.pose_position == 'POSE'
    print('Exported %d parts, skipped %r' % (count, skipped))
    assert count == 6, count

    # --- read the .glb back ------------------------------------------------------
    with open(out_path, 'rb') as f:
        data = f.read()
    magic, version, length = struct.unpack_from('<III', data, 0)
    assert magic == 0x46546C67
    chunk_len, chunk_type = struct.unpack_from('<II', data, 12)
    gltf = json.loads(data[20:20 + chunk_len])
    nodes = gltf['nodes']
    skins = gltf.get('skins', [])
    assert len(skins) == 1, skins
    joints = [nodes[j]['name'] for j in skins[0]['joints']]
    print('glTF joints:', joints)
    assert sorted(joints) == sorted(bones + ['root']), joints
    roots = [j for j in skins[0]['joints'] if not any(j in nodes[p].get('children', []) for p in skins[0]['joints'])]
    assert [nodes[j]['name'] for j in roots] == ['root'], [nodes[j]['name'] for j in roots]
    skinned = [n['name'] for n in nodes if 'mesh' in n and 'skin' in n]
    print('skinned meshes:', skinned)
    assert len(skinned) == 6, skinned
    for m in gltf['meshes']:
        for p in m['primitives']:
            assert 'JOINTS_0' in p['attributes'] and 'WEIGHTS_0' in p['attributes']

    def mat(n):
        from mathutils import Matrix, Quaternion, Vector
        if 'matrix' in n:
            m = n['matrix']
            return Matrix([m[0:4], m[4:8], m[8:12], m[12:16]]).transposed()
        t = Matrix.Translation(Vector(n.get('translation', (0, 0, 0))))
        r = n.get('rotation', (0, 0, 0, 1))
        r = Quaternion((r[3], r[0], r[1], r[2])).to_matrix().to_4x4()
        s = n.get('scale', (1, 1, 1))
        return t @ r @ Matrix.Diagonal((s[0], s[1], s[2], 1.0))

    parent = {}
    for i, n in enumerate(nodes):
        for c in n.get('children', []):
            parent[c] = i

    def world(i):
        m = mat(nodes[i])
        while i in parent:
            i = parent[i]
            m = mat(nodes[i]) @ m
        return m

    # Unreal's importer turns glTF's Y-up metres into Z-up centimetres
    from car_rig.xmath import Vec, Quat, Xform  # noqa: E402
    from car_rig.layout import build_layout  # noqa: E402

    ue_bones = {}
    for j in skins[0]['joints']:
        if nodes[j]['name'] == 'root':
            continue
        p = world(j).to_translation()
        ue_bones[nodes[j]['name']] = Xform(Quat(), Vec(p.x * 100, -p.z * 100, p.y * 100))
    lay = build_layout(ue_bones)
    print('wheel radii:', [round(w.radius, 1) for w in lay.wheels], 'wheelbase:', round(lay.wheelbase, 1))
    assert all(abs(w.radius - R * 100) < 0.5 for w in lay.wheels)
    assert abs(lay.wheelbase - 280.0) < 0.5
    print('BLENDER EXPORT OK')


if __name__ == '__main__':
    code = 1
    try:
        main()
        code = 0
    except Exception:
        import traceback
        traceback.print_exc()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
