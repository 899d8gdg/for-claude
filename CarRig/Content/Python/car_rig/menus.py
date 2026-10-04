"""Editor menus: right-click a Skeletal Mesh > Generate Car Rig, and a Car Rig menu in the main menu bar."""

import traceback

import unreal

OWNER = 'CarRig'
MAIN_MENU = 'LevelEditor.MainMenu'
CAR_MENU = 'LevelEditor.MainMenu.CarRig'
MESH_MENU = 'ContentBrowser.AssetContextMenu.SkeletalMesh'

_entries = []


def _message(text, title='Car Rig'):
    unreal.EditorDialog.show_message(title, text, unreal.AppMsgType.OK)


def _run(action):
    from . import generator, bake
    try:
        lines = action()
        if lines:
            _message('\n'.join(lines))
    except (generator.GenerateError, bake.BakeError) as e:
        _message(str(e))
    except Exception:
        text = traceback.format_exc()
        unreal.log_error('Car Rig failed:\n' + text)
        _message('Something went wrong. The full error is in the Output Log (Window > Output Log):\n\n'
                 + text.strip().splitlines()[-1])


def generate_selected():
    from . import generator
    meshes = [a for a in unreal.EditorUtilityLibrary.get_selected_assets() if isinstance(a, unreal.SkeletalMesh)]
    if not meshes:
        raise generator.GenerateError('Select the car\'s Skeletal Mesh in the Content Browser first.')
    lines = []
    for mesh in meshes:
        bp, lay, count = generator.generate(mesh)
        lines.append('Created %s: %d wheels, %d controls.' % (
            bp.get_path_name().split('.')[0], len(lay.wheels),
            len([e for e in lay.elements if e.kind == 'control'])))
        lines += ['  Note: ' + w for w in lay.warnings]
    lines.append('')
    lines.append('Add the car to a Level Sequence, then + Track > Control Rig and pick the CR_ asset.')
    return lines


def bake_wheels():
    from . import bake
    return ['Baked wheel rotation.'] + bake.bake_wheels()


def bake_steering():
    from . import bake
    return ['Baked steering.'] + bake.bake_steering()


def clear_baked():
    from . import bake
    return bake.clear_baked()


@unreal.uclass()
class CarRigGenerateEntry(unreal.ToolMenuEntryScript):
    @unreal.ufunction(override=True)
    def execute(self, context):
        _run(generate_selected)


@unreal.uclass()
class CarRigBakeWheelsEntry(unreal.ToolMenuEntryScript):
    @unreal.ufunction(override=True)
    def execute(self, context):
        _run(bake_wheels)


@unreal.uclass()
class CarRigBakeSteeringEntry(unreal.ToolMenuEntryScript):
    @unreal.ufunction(override=True)
    def execute(self, context):
        _run(bake_steering)


@unreal.uclass()
class CarRigClearEntry(unreal.ToolMenuEntryScript):
    @unreal.ufunction(override=True)
    def execute(self, context):
        _run(clear_baked)


def _add(cls, menu, menu_name, section, name, label, tip):
    entry = cls()
    entry.init_entry(OWNER, menu_name, section, name, label, tip)
    menu.add_menu_entry_object(entry)
    _entries.append(entry)


def register():
    menus = unreal.ToolMenus.get()

    mesh_menu = menus.extend_menu(MESH_MENU)
    _add(CarRigGenerateEntry, mesh_menu, MESH_MENU, 'GetAssetActions', 'CarRigGenerate', 'Generate Car Rig',
         'Build a Rigacar-style car Control Rig for this mesh (CR_ asset next to it).')

    main = menus.extend_menu(MAIN_MENU)
    car = main.add_sub_menu(OWNER, '', 'CarRig', 'Car Rig', 'Rigacar-style car rigging')
    _add(CarRigGenerateEntry, car, CAR_MENU, 'Rig', 'Generate', 'Generate Car Rig for Selected Mesh',
         'Select the car Skeletal Mesh in the Content Browser first.')
    _add(CarRigBakeWheelsEntry, car, CAR_MENU, 'Animation', 'BakeWheels', 'Bake Wheels Rotation',
         'Key the wheel rotation channels from the car\'s motion in the open Level Sequence.')
    _add(CarRigBakeSteeringEntry, car, CAR_MENU, 'Animation', 'BakeSteering', 'Bake Steering',
         'Key the Steering_rotation channel so the front wheels follow the path.')
    _add(CarRigClearEntry, car, CAR_MENU, 'Animation', 'Clear', 'Clear Baked Animation',
         'Remove the baked wheel and steering keys.')
    menus.refresh_all_widgets()
