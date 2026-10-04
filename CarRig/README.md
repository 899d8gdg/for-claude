# Car Rig for Unreal Engine 5 (Rigacar-style)

Rig a car in Blender with Rigacar, export it, then in Unreal right-click the
car's Skeletal Mesh and choose **Generate Car Rig**. You get a Control Rig with
Rigacar's controls: Root, Drift, Steering, Suspension, wheel dampers, ground
sensors that follow the terrain, and wheels that spin as the car drives. Two
Sequencer bake commands match Rigacar's **Bake wheels rotation** and **Bake car
steering**.

The plugin is written in Python, so there is nothing to compile and no Visual
Studio needed. It was written for Unreal 5.8 and checked against the 5.6 Python
API. It has not been run inside the Unreal Editor yet (see
[Status](#status)).

## 1. Blender: rig and export

1. Model the car facing **-Y**, standing on the ground at **Z = 0**, the way
   Rigacar expects. Name the parts so Rigacar can find them: `Body`,
   `Wheel.Ft.L`, `Wheel.Ft.R`, `Wheel.Bk.L`, `Wheel.Bk.R`. Add
   `WheelBrake.Ft.L` and similar for brake callipers if you have them.
2. Select the parts, then **Add > Armature > Car (deformation rig)**. Rigacar
   creates the `DEF-` bones and attaches each part to its bone.
   You don't need to press Rigacar's **Generate**. Unreal builds the controls.
3. Install the exporter: **Edit > Preferences > Add-ons > Install from Disk**,
   pick `CarRig/Blender/rigacar_to_unreal.py`, and tick it.
4. Select the Rigacar armature, then **File > Export > Rigacar Car for Unreal
   (.glb)**.

The exporter is needed because Rigacar parents the parts to bones, while
Unreal only makes a Skeletal Mesh from skinned geometry. It skins temporary
copies of the parts to their bones, adds a single `root` bone, exports the
`DEF-` bones in their rest pose, then deletes the copies. Your Blender scene
stays as it was.

Exporting by hand also works if the meshes are already skinned to the `DEF-`
bones. Use glTF Binary with **Deformation Bones Only** and no animation, and
make sure there is a single root bone.

## 2. Unreal: install the plugin

1. Close the editor. Copy the whole `CarRig` folder into your project's
   `Plugins` folder, so you have `YourProject/Plugins/CarRig/CarRig.uplugin`.
2. Open the project. If the plugin isn't on yet, go to **Edit > Plugins**,
   search for **Car Rig**, tick it and restart. It switches on **Python Editor
   Script Plugin**, **Control Rig**, **Editor Scripting Utilities** and
   **Sequencer Scripting** for you.

When it loads, the Output Log shows `Car Rig: menus added`, and a **Car Rig**
menu appears in the main menu bar.

## 3. Generate the rig

1. Drag the `.glb` into the Content Browser. It imports as a Skeletal Mesh.
2. Right-click the Skeletal Mesh and choose **Generate Car Rig**.

This creates `CR_<name>` next to the mesh. Running it again rebuilds the same
asset, so Sequencer tracks that use it keep working.

## 4. Animate

1. Put the car in the level, set its **Collision Presets** to **NoCollision**
   (see [Ground detection](#ground-detection)), and add it to a Level Sequence.
2. On the car's track, choose **+ Track > Control Rig** and pick `CR_<name>`.
3. Animate the controls:

| Control | What it does |
| --- | --- |
| `Root` | Moves the whole car. Sits on the ground under the back axle. |
| `Drift` | Rotate (yaw) to swing the car around its front axle. The front wheels keep pointing at `Steering`, so they counter-steer. |
| `Steering` | Move sideways to steer the front wheels. |
| `Suspension` | Move forward/back to pitch the body, sideways to roll it, and up/down to bounce it. |
| `WheelDamper_Ft_L` ... | Move up/down to lift or drop one corner of the body. The wheels stay where they are. |
| `GroundSensor_Ft_L` ... | Raise or lower one wheel. Sits under the wheel and follows the ground. |
| `GroundSensor_Axle_Ft` / `_Bk` | Raise or lower a whole axle. Follows the ground. |
| `Wheel_Ft_L` ... | Rotate to spin a wheel by hand. |
| `WheelBrake_Ft` / `_Bk` | 0 = rolling, 1 = locked. Used by **Bake Wheels Rotation**. |

Settings are animation channels on `Root`. They work like Rigacar's custom
properties:

| Channel | Default | |
| --- | --- | --- |
| `SuspensionFactor` | 0.5 | How much the dampers and ground pitch the body |
| `SuspensionRollingFactor` | 0.5 | How much they roll it |
| `WheelsOnXAxis` | 0 | 1 = wheels spin as `Root` moves forward, with no baking (straight lines only) |
| `GroundDetection` | 1 | 0 turns ground following off, for jumps |
| `GroundSensorLimit` | 20 | How far (cm) a wheel can move up or down to reach the ground |
| `GroundTraceDepth` | 200 | How far (cm) below the axles to look for ground |

### Baking wheels and steering

Animate `Root` (and `Drift`) along your path, keep the Level Sequence open, then
use the **Car Rig** menu:

- **Bake Wheels Rotation** keys each wheel's `Wheel_*_rotation` channel so the
  wheel turns exactly as far as it travels, including reversing and braking.
- **Bake Steering** keys `Steering_rotation` so the front wheels point along the
  path.
- **Clear Baked Animation** removes those keys.

Baking uses the sequence's playback range. Re-bake whenever you change the path.

## Ground detection

Each ground sensor sends a short trace straight down (Visibility channel) and
sits the wheel on whatever it hits. Rigacar does the same with its
shrinkwrap-to-ground objects. Two things to know:

- **The car must not block the trace itself.** Set the car actor's Collision
  Presets to **NoCollision**, or make sure its physics asset doesn't block
  Visibility. Otherwise a wheel can sit on top of its own collision and jump
  up.
- Ground following holds the car down. To make it jump, key
  `GroundDetection` to 0 for those frames.

## Bone names

The generator looks for Rigacar's deformation bones:

- `DEF-Body`
- `DEF-Wheel.Ft.L`, `DEF-Wheel.Ft.R`, `DEF-Wheel.Bk.L`, `DEF-Wheel.Bk.R`
- extra pairs `DEF-Wheel.Bk.L.001` and so on (trucks)
- optional `DEF-WheelBrake.Ft.L` and so on

Dots, dashes, underscores and capitals don't matter, so `def_wheel_ft_l` works
too. The car can face any direction. Wheel size is taken from each wheel bone's
height above the ground. Any other bones parented under `DEF-Body`, such as
doors, follow the body.

## Status

What has been checked, and what hasn't:

- The rig logic has been tested outside Unreal: driving, wheel spin direction,
  steering, drift counter-steer, bumps, holes, ramps, raised ground, suspension,
  dampers and a six-wheel truck. The tests evaluate the exact graph the plugin
  builds, in Python, the way Control Rig runs it.
- Every Unreal call the plugin makes was checked against Unreal's real 5.6 Python
  API (function and argument names, struct properties, enum values, node types
  and pins).
- The Blender exporter was tested with Rigacar and Blender 4.5. The .glb it
  writes has a single root, the `DEF-` bones and skinned meshes.
- **It has not been run inside the Unreal Editor yet.** If Generate or a bake
  shows an error, open **Window > Output Log**, copy the red `Car Rig` lines,
  and send them over. The generator stops with a clear message that names the
  node or pin involved.

The control shapes' sizes are estimates. If they look too big or small,
change `SHAPE_UNIT` at the top of `layout.py` and generate again.

## Files

- `CarRig.uplugin` is the plugin description.
- `Content/Python/init_unreal.py` adds the menus when the editor starts.
- `Content/Python/car_rig/` contains the generator:
  - `layout.py` works out where every control goes from the bones.
  - `solve.py` is the Forward Solve graph, which does Rigacar's constraints.
  - `graph.py` builds the graph through Unreal's RigVM controller.
  - `generator.py` creates the asset.
  - `bake.py` is the Sequencer baking.
  - `menus.py` adds the menus.
- `Blender/rigacar_to_unreal.py` is the Blender exporter.

The tests live in `tests/car_rig` at the repository root:

```sh
tests/car_rig/fetch_stub.sh          # downloads Unreal's 5.6 Python API stub
python3 -m unittest discover -s tests/car_rig
```

`tests/car_rig/blender_export_check.py` runs the Blender exporter on a toy car
rigged with Rigacar. Run it with Blender's Python (`pip install bpy`); usage is
at the top of the file.

Inspired by [Rigacar](https://github.com/digicreatures/rigacar). This is a
separate implementation for Unreal; no Rigacar code is included.
