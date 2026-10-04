# Unreal runs this file when the editor starts, because the CarRig plugin is enabled.
import unreal

try:
    from car_rig import menus
    menus.register()
    unreal.log('Car Rig: menus added (right-click a Skeletal Mesh > Generate Car Rig, or the Car Rig menu).')
except Exception as error:  # never stop the editor from starting
    import traceback
    unreal.log_error('Car Rig: could not add menus: %s\n%s' % (error, traceback.format_exc()))
