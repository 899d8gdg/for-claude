"""Runs the plugin's Unreal code (Generate, menus, baking) against fake_unreal.

Every Unreal call is checked against the real 5.6 API stub; run fetch_stub.sh
first. The generated rig is then evaluated to check it moves the car.
"""

import math
import unittest

import stubsig

if stubsig.available():
    import fake_unreal
    unreal = fake_unreal.install()
    from car_rig import generator, menus, bake

from carfixture import car_bones, RADIUS
from interp import angle_deg
from car_rig.xmath import Vec, Quat, Xform, X, Z


@unittest.skipUnless(stubsig.available(), 'run tests/car_rig/fetch_stub.sh first')
class UnrealTest(unittest.TestCase):
    def setUp(self):
        fake_unreal.ASSETS.clear()
        fake_unreal.dialogs[:] = []
        fake_unreal.logs[:] = []
        self.bones, self.parents, self.car_rot = car_bones()
        lo = Vec(-200.0, -300.0, 0.0)
        hi = Vec(200.0, 300.0, 150.0)
        self.mesh = unreal.SkeletalMesh('SK_Car', '/Game/Cars', self.bones, self.parents, (lo, hi))

    def generate(self):
        bp, lay, count = generator.generate(self.mesh)
        bp.rig.ground = lambda x, y: 0.0
        return bp, lay, count

    def solve(self, bp, values=None, channels=None):
        bp.rig.reset()
        for name, xf in (values or {}).items():
            bp.rig.find(('Control', name)).value = xf
        ev = bp.evaluator()
        ev.channel_defaults.update(channels or {})
        ev.run()

    def bone(self, bp, name):
        return bp.rig.global_(bp.rig.find(('Bone', name)))

    def assertVec(self, a, b, tol=0.01):
        self.assertLess((Vec(*a) - Vec(*b)).length(), tol, '%r != %r' % (a, b))


class Generate(UnrealTest):
    def test_creates_asset_next_to_mesh(self):
        bp, lay, count = self.generate()
        self.assertEqual(bp.get_path_name(), '/Game/Cars/CR_Car.CR_Car')
        self.assertIs(bp.preview, self.mesh)
        self.assertEqual(bp.compiled, 1)
        self.assertGreater(count, 100)

    def test_controls_match_rigacar(self):
        bp, lay, _ = self.generate()
        controls = {e.name for e in bp.rig.order if e.kind == 'Control'}
        for name in ('Root', 'Drift', 'Steering', 'Suspension', 'GroundSensor_Axle_Ft', 'GroundSensor_Axle_Bk',
                     'Wheel_Ft_L', 'Wheel_Bk_R', 'GroundSensor_Ft_L', 'WheelDamper_Bk_R', 'WheelBrake_Ft'):
            self.assertIn(name, controls)
        channels = {c['display'] for c in bp.channels.values()}
        for name in ('SuspensionFactor', 'SuspensionRollingFactor', 'WheelsOnXAxis', 'GroundDetection',
                     'Steering_rotation', 'Wheel_Ft_L_rotation'):
            self.assertIn(name, channels)

    def test_rest_pose(self):
        bp, lay, _ = self.generate()
        self.solve(bp)
        for name, rest in self.bones.items():
            self.assertVec(self.bone(bp, name).trans, rest.trans)
            self.assertLess(angle_deg(rest.rot.inverse() * self.bone(bp, name).rot), 0.05, name)

    def test_drive_and_steer(self):
        bp, lay, _ = self.generate()
        fwd = self.car_rot.rotate(X)
        ls = lay.steering_distance
        self.solve(bp, {'Root': Xform(trans=Vec(120.0, 0.0, 0.0)),
                        'Steering': Xform(trans=Vec(0.0, ls * math.tan(math.radians(25.0)), 0.0))})
        body = self.bone(bp, 'DEF-Body')
        self.assertVec(body.trans, self.bones['DEF-Body'].trans + fwd * 120.0)
        d = self.bone(bp, 'DEF-Wheel.Ft.R').rot * self.bones['DEF-Wheel.Ft.R'].rot.inverse()
        self.assertAlmostEqual(angle_deg(d), 25.0, places=2)

    def test_regenerate_replaces_rig(self):
        bp, lay, count = self.generate()
        n_elements = len(bp.rig.order)
        n_channels = len(bp.channels)
        bp2, lay2, count2 = self.generate()
        self.assertIs(bp, bp2)
        self.assertEqual(len(bp.rig.order), n_elements)
        self.assertEqual(len(bp.channels), n_channels)
        self.assertEqual(len(bp.controller.nodes), count2)
        self.solve(bp)
        self.assertVec(self.bone(bp, 'DEF-Body').trans, self.bones['DEF-Body'].trans)

    def test_rejects_mesh_without_wheels(self):
        mesh = unreal.SkeletalMesh('SK_Box', '/Game', {'Root': Xform(), 'Bone': Xform(trans=Vec(0, 0, 10))}, {})
        with self.assertRaises(generator.GenerateError):
            generator.generate(mesh)

    def test_existing_asset_of_other_type(self):
        fake_unreal.ASSETS['/Game/Cars/CR_Car'] = unreal.Object('CR_Car')
        with self.assertRaises(generator.GenerateError):
            generator.generate(self.mesh)


class Menus(UnrealTest):
    def test_register_and_generate_from_menu(self):
        menus.register()
        mesh_menu = fake_unreal.MENUS.menus[menus.MESH_MENU]
        car_menu = fake_unreal.MENUS.menus[menus.CAR_MENU]
        self.assertEqual([e.label for e in mesh_menu.entries], ['Generate Car Rig'])
        self.assertEqual(len(car_menu.entries), 4)
        fake_unreal.SELECTED[:] = [self.mesh]
        mesh_menu.entries[0].execute(None)
        self.assertIn('Created /Game/Cars/CR_Car', fake_unreal.dialogs[-1])

    def test_generate_with_nothing_selected(self):
        fake_unreal.SELECTED[:] = []
        menus._run(menus.generate_selected)
        self.assertIn('Select the car', fake_unreal.dialogs[-1])

    def test_unexpected_error_is_reported(self):
        def boom():
            raise RuntimeError('kaboom')
        menus._run(boom)
        self.assertIn('kaboom', fake_unreal.dialogs[-1])
        self.assertEqual(fake_unreal.logs[-1][0], 'error')


class Bake(UnrealTest):
    def sequence(self, bp, animate, frames=40):
        seq = unreal.LevelSequence(0, frames, animate)
        names = list(bp.channels)
        track = fake_unreal._Track(seq, names)
        unreal.ControlRigSequencerLibrary.proxies = [
            unreal.ControlRigSequencerBindingProxy(unreal.ControlRig(bp), track)]
        unreal.LevelSequenceEditorBlueprintLibrary.current = seq
        return seq

    def test_bake_wheels_straight(self):
        bp, lay, _ = self.generate()
        seq = self.sequence(bp, lambda f: {'Root': Xform(trans=Vec(10.0 * f, 0.0, 0.0))})
        bake.bake_wheels()
        keys = seq.keys['Wheel_Bk_L_rotation']
        self.assertAlmostEqual(keys[0], 0.0)
        self.assertAlmostEqual(keys[39], 390.0 / RADIUS, places=3)

    def test_bake_wheels_with_brake(self):
        bp, lay, _ = self.generate()
        seq = self.sequence(bp, lambda f: {'Root': Xform(trans=Vec(10.0 * f, 0.0, 0.0))})
        seq.keys['WheelBrake_Ft'] = {0: 1.0}
        bake.bake_wheels()
        self.assertAlmostEqual(seq.keys['Wheel_Ft_L_rotation'][39], 0.0)
        self.assertAlmostEqual(seq.keys['Wheel_Bk_L_rotation'][39], 390.0 / RADIUS, places=3)

    def test_bake_wheels_reverse(self):
        bp, lay, _ = self.generate()
        seq = self.sequence(bp, lambda f: {'Root': Xform(trans=Vec(-5.0 * f, 0.0, 0.0))})
        bake.bake_wheels()
        self.assertLess(seq.keys['Wheel_Ft_R_rotation'][39], 0.0)

    def test_bake_steering_follows_a_curve(self):
        bp, lay, _ = self.generate()
        radius = 1500.0

        def animate(f):
            # drive along a circle turning right, Root facing along the path
            a = f * 0.02
            pos = Vec(radius * math.sin(a), radius * (1.0 - math.cos(a)), 0.0)
            return {'Root': Xform(Quat.from_axis_angle(Z, a), pos)}
        seq = self.sequence(bp, animate)
        bake.bake_steering()
        keys = seq.keys['Steering_rotation']
        mid = keys[20]
        self.assertGreater(mid, 0.0, 'turning right should push Steering to the right')
        # with the baked value the front wheels aim along the front axle's path
        expected = math.degrees(math.atan2(mid, lay.steering_distance))
        wheelbase = lay.wheelbase
        ideal = math.degrees(math.atan2(wheelbase, radius))
        self.assertAlmostEqual(expected, ideal, delta=1.0)

    def test_clear_baked(self):
        bp, lay, _ = self.generate()
        seq = self.sequence(bp, lambda f: {'Root': Xform(trans=Vec(10.0 * f, 0.0, 0.0))})
        bake.bake_wheels()
        bake.clear_baked()
        self.assertFalse(any(seq.keys.get(n) for n in seq.keys if n.endswith('_rotation')))

    def test_no_sequence(self):
        unreal.LevelSequenceEditorBlueprintLibrary.current = None
        with self.assertRaises(bake.BakeError):
            bake.bake_wheels()


if __name__ == '__main__':
    unittest.main()
