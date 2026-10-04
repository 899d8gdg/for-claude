"""Blender: export a Rigacar car as a .glb that Unreal imports as a Skeletal Mesh.

Rigacar attaches the car's parts to its bones with bone parenting, but Unreal
only builds a Skeletal Mesh from skinned geometry. This exporter skins copies
of the parts to their bones (each part follows one bone 100%), exports the
deformation bones and the copies, then deletes the copies. Your scene is left
as it was.

Install: Edit > Preferences > Add-ons > Install from Disk (pick this file),
or open it in the Scripting tab and press Run Script.
Use: select the Rigacar armature, then File > Export > Rigacar Car for Unreal (.glb).
"""

bl_info = {
    "name": "Rigacar Car for Unreal (.glb)",
    "author": "Car Rig for Unreal",
    "version": (1, 0, 0),
    "blender": (2, 93, 0),
    "location": "File > Export > Rigacar Car for Unreal (.glb)",
    "description": "Export a Rigacar car as a skinned glTF for Unreal's Car Rig generator",
    "category": "Import-Export",
}

import bpy
from bpy.props import StringProperty
from bpy_extras.io_utils import ExportHelper


def find_armature(context):
    ob = context.active_object
    if ob and ob.type == 'ARMATURE':
        return ob
    for ob in context.selected_objects:
        if ob.type == 'ARMATURE':
            return ob
    return None


def car_parts(arm):
    """(object, bone name) for every mesh that follows a bone of `arm`.

    A part is bone-parented to the armature, or is a child of such a part.
    """
    parts = []
    skipped = []

    def walk(ob, bone):
        if ob.parent is arm and ob.parent_type == 'BONE':
            bone = ob.parent_bone
        if bone:
            if ob.type == 'MESH':
                parts.append((ob, bone))
            elif ob.type != 'EMPTY' or ob.instance_type == 'COLLECTION':
                skipped.append(ob.name)
        for child in ob.children:
            walk(child, bone)

    for child in arm.children:
        walk(child, None)
    return parts, skipped


def armature_copy(arm, collection, view_layer):
    """A constraint-free copy of `arm` in its rest pose, with one root bone.

    Unreal needs a single root bone; Rigacar's DEF- bones have no parent.
    """
    data = arm.data.copy()
    data.pose_position = 'REST'
    copy = arm.copy()
    copy.data = data
    copy.name = arm.name + '_unreal'
    copy.animation_data_clear()
    collection.objects.link(copy)
    for pb in copy.pose.bones:
        for c in list(pb.constraints):
            pb.constraints.remove(c)
    roots = [b.name for b in data.bones if b.parent is None and b.use_deform]
    if len(roots) > 1:
        for ob in view_layer.objects:
            ob.select_set(False)
        copy.select_set(True)
        view_layer.objects.active = copy
        bpy.ops.object.mode_set(mode='EDIT')
        root = data.edit_bones.new('root')
        root.head = (0.0, 0.0, 0.0)
        root.tail = (0.0, 0.25, 0.0)
        root.use_deform = True
        for name in roots:
            data.edit_bones[name].parent = root
        bpy.ops.object.mode_set(mode='OBJECT')
    return copy


def skinned_copy(ob, bone, arm, collection):
    copy = ob.copy()
    copy.data = ob.data.copy()
    copy.name = ob.name + '_unreal'
    collection.objects.link(copy)
    world = ob.matrix_world.copy()
    copy.parent = arm
    copy.parent_type = 'OBJECT'
    copy.matrix_parent_inverse = arm.matrix_world.inverted()
    copy.matrix_world = world
    copy.vertex_groups.clear()
    group = copy.vertex_groups.new(name=bone)
    group.add(range(len(copy.data.vertices)), 1.0, 'REPLACE')
    for mod in [m for m in copy.modifiers if m.type == 'ARMATURE']:
        copy.modifiers.remove(mod)
    mod = copy.modifiers.new('Armature', 'ARMATURE')
    mod.object = arm
    return copy


def export_car(context, arm, filepath):
    """Export `arm` and its parts to `filepath`. Returns (part count, skipped names)."""
    parts, skipped = car_parts(arm)
    if not parts:
        raise RuntimeError('No meshes are attached to the bones of %s. Use Rigacar\'s car deformation rig '
                           'with the car parts parented to its bones.' % arm.name)
    view_layer = context.view_layer
    collection = context.scene.collection
    pose_position = arm.data.pose_position
    selected = list(context.selected_objects)
    active = view_layer.objects.active
    copies = []
    rig = None
    try:
        arm.data.pose_position = 'REST'
        view_layer.update()
        rig = armature_copy(arm, collection, view_layer)
        copies = [skinned_copy(ob, bone, rig, collection) for ob, bone in parts]
        view_layer.update()
        for ob in context.selected_objects:
            ob.select_set(False)
        rig.select_set(True)
        for c in copies:
            c.select_set(True)
        view_layer.objects.active = rig
        options = dict(
            filepath=filepath,
            export_format='GLB',
            use_selection=True,
            export_def_bones=True,      # DEF- bones only, not Rigacar's controls
            export_animations=False,
            export_apply=True,
            export_skins=True,
            export_yup=True,
        )
        props = bpy.ops.export_scene.gltf.get_rna_type().properties.keys()
        bpy.ops.export_scene.gltf(**{k: v for k, v in options.items() if k in props})
    finally:
        for c in copies:
            data = c.data
            bpy.data.objects.remove(c, do_unlink=True)
            if data.users == 0:
                bpy.data.meshes.remove(data)
        if rig is not None:
            data = rig.data
            bpy.data.objects.remove(rig, do_unlink=True)
            if data.users == 0:
                bpy.data.armatures.remove(data)
        arm.data.pose_position = pose_position
        for ob in context.selected_objects:
            ob.select_set(False)
        for ob in selected:
            if ob.name in view_layer.objects:
                ob.select_set(True)
        view_layer.objects.active = active
    return len(parts), skipped


class EXPORT_OT_rigacar_unreal(bpy.types.Operator, ExportHelper):
    bl_idname = "export_scene.rigacar_unreal"
    bl_label = "Export Car for Unreal"
    bl_description = "Export the selected Rigacar car as a skinned .glb for Unreal's Car Rig generator"
    filename_ext = ".glb"
    filter_glob: StringProperty(default="*.glb", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return find_armature(context) is not None

    def execute(self, context):
        arm = find_armature(context)
        if 'DEF-Body' not in arm.data.bones:
            self.report({'ERROR'}, '%s has no DEF-Body bone. Select the Rigacar armature.' % arm.name)
            return {'CANCELLED'}
        try:
            count, skipped = export_car(context, arm, self.filepath)
        except RuntimeError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        if skipped:
            self.report({'WARNING'}, 'Not exported (convert to mesh or make instances real first): '
                        + ', '.join(skipped))
        self.report({'INFO'}, 'Exported %d car parts to %s' % (count, self.filepath))
        return {'FINISHED'}


def menu_func(self, context):
    self.layout.operator(EXPORT_OT_rigacar_unreal.bl_idname, text="Rigacar Car for Unreal (.glb)")


def register():
    bpy.utils.register_class(EXPORT_OT_rigacar_unreal)
    bpy.types.TOPBAR_MT_file_export.append(menu_func)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu_func)
    bpy.utils.unregister_class(EXPORT_OT_rigacar_unreal)


if __name__ == "__main__":
    register()
