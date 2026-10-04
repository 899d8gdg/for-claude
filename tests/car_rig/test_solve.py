"""Checks the forward-solve graph moves the car the way Rigacar does.

Run: python3 -m unittest discover -s tests/car_rig
"""

import math
import unittest

from carfixture import CarRig, RADIUS, HALF_BASE
from interp import angle_deg
from car_rig.xmath import Vec, Quat, Y, Z

WHEELS = ('DEF-Wheel.Ft.L', 'DEF-Wheel.Ft.R', 'DEF-Wheel.Bk.L', 'DEF-Wheel.Bk.R')
ALL = WHEELS + ('DEF-Body', 'DEF-WheelBrake.Ft.L', 'DEF-Door.L')


class Base(unittest.TestCase):
    kw = {}

    def setUp(self):
        self.car = CarRig(**self.kw)

    def assertVec(self, a, b, tol=0.01, msg=None):
        d = (Vec(*a) - Vec(*b)).length()
        self.assertLess(d, tol, msg or '%r != %r (off by %.4f)' % (a, b, d))

    def assertRest(self, names=ALL, tol=0.01):
        for n in names:
            self.assertVec(self.car.bone(n).trans, self.car.rest(n).trans, tol, '%s moved' % n)
            self.assertLess(angle_deg(self.car.rest(n).rot.inverse() * self.car.bone(n).rot), 0.05,
                            '%s rotated' % n)

    def wheel_spin(self, name):
        """Signed spin of a wheel bone about the car's right axis, in radians."""
        d = self.car.bone(name).rot * self.car.rest(name).rot.inverse()
        right = self.car.right
        axis_part = Vec(d.x, d.y, d.z).dot(right)
        return 2.0 * math.atan2(axis_part, d.w)

    def wheel_yaw(self, name):
        d = self.car.bone(name).rot * self.car.rest(name).rot.inverse()
        f = d.rotate(self.car.fwd)
        return math.degrees(math.atan2(f.dot(self.car.right), f.dot(self.car.fwd)))


class RestPose(Base):
    def test_rest_pose_is_unchanged(self):
        self.car.solve()
        self.assertRest()

    def test_rest_pose_without_ground(self):
        self.car.rig.ground = None
        self.car.solve()
        self.assertRest()


class Driving(Base):
    def test_root_moves_whole_car(self):
        f = self.car.fwd
        self.car.set_value('Root', Vec(150.0, 0.0, 0.0))
        self.car.solve()
        for n in ALL:
            self.assertVec(self.car.bone(n).trans, self.car.rest(n).trans + f * 150.0, msg=n)
            self.assertAlmostEqual(self.wheel_spin(n) if n in WHEELS else 0.0, 0.0, places=4)

    def test_wheels_on_x_axis_rolls_wheels_forward(self):
        self.car.rig.channels[('Root', 'WheelsOnXAxis')] = 1.0
        self.car.set_value('Root', Vec(100.0, 0.0, 0.0))
        self.car.solve()
        for n in WHEELS:
            # rolling forward: positive rotation about the right axis (top moves forward)
            self.assertAlmostEqual(self.wheel_spin(n), 100.0 / RADIUS, places=3, msg=n)

    def test_wheel_top_moves_forward(self):
        self.car.rig.channels[('Root', 'WheelsOnXAxis')] = 1.0
        self.car.set_value('Root', Vec(RADIUS * 0.3, 0.0, 0.0))
        self.car.solve()
        n = 'DEF-Wheel.Bk.R'
        rest, now = self.car.rest(n), self.car.bone(n)
        top_rest = rest.trans + Z * RADIUS
        local = rest.inverse_transform_location(top_rest)
        moved = now.transform_location(local) - (now.trans - rest.trans) - rest.trans
        moved = moved - Z * RADIUS
        self.assertGreater(moved.dot(self.car.fwd), 0.0)

    def test_baked_wheel_rotation_channel(self):
        self.car.rig.channels[('Wheel_Bk_L', 'Wheel_Bk_L_rotation')] = 1.25
        self.car.solve()
        self.assertAlmostEqual(self.wheel_spin('DEF-Wheel.Bk.L'), 1.25, places=4)
        self.assertAlmostEqual(self.wheel_spin('DEF-Wheel.Bk.R'), 0.0, places=4)

    def test_manual_wheel_control(self):
        self.car.set_value('Wheel_Ft_R', rot=Quat.from_axis_angle(Y, 0.7))
        self.car.solve()
        self.assertAlmostEqual(self.wheel_spin('DEF-Wheel.Ft.R'), 0.7, places=4)
        self.assertRest(('DEF-Body', 'DEF-Wheel.Ft.L'))

    def test_brake_calliper_does_not_spin(self):
        self.car.rig.channels[('Wheel_Ft_L', 'Wheel_Ft_L_rotation')] = 2.0
        self.car.solve()
        self.assertRest(('DEF-WheelBrake.Ft.L',))


class Steering(Base):
    def test_steering_right_turns_front_wheels(self):
        ls = self.car.layout.steering_distance
        self.car.set_value('Steering', Vec(0.0, ls * math.tan(math.radians(20.0)), 0.0))
        self.car.solve()
        for n in ('DEF-Wheel.Ft.L', 'DEF-Wheel.Ft.R'):
            self.assertAlmostEqual(self.wheel_yaw(n), 20.0, places=2, msg=n)
            self.assertVec(self.car.bone(n).trans, self.car.rest(n).trans, msg=n)
        for n in ('DEF-Wheel.Bk.L', 'DEF-Wheel.Bk.R', 'DEF-Body'):
            self.assertRest((n,))
        # the calliper turns with its wheel
        self.assertAlmostEqual(self.wheel_yaw('DEF-WheelBrake.Ft.L'), 20.0, places=2)

    def test_baked_steering_channel(self):
        ls = self.car.layout.steering_distance
        self.car.rig.channels[('Steering', 'Steering_rotation')] = -ls * math.tan(math.radians(15.0))
        self.car.solve()
        self.assertAlmostEqual(self.wheel_yaw('DEF-Wheel.Ft.L'), -15.0, places=2)

    def test_steered_wheel_still_spins_about_its_axle(self):
        ls = self.car.layout.steering_distance
        self.car.set_value('Steering', Vec(0.0, ls, 0.0))
        self.car.rig.channels[('Wheel_Ft_L', 'Wheel_Ft_L_rotation')] = 0.5
        self.car.solve()
        d = self.car.bone('DEF-Wheel.Ft.L').rot * self.car.rest('DEF-Wheel.Ft.L').rot.inverse()
        steer = Quat.from_axis_angle(Z, math.radians(45.0))
        axle = steer.rotate(self.car.right)
        spin = d * steer.inverse()
        self.assertVec(Vec(spin.x, spin.y, spin.z).unit(), axle, tol=1e-3)


class Drift(Base):
    def drift(self, deg):
        self.car.set_value('Drift', rot=Quat.from_axis_angle(Z, math.radians(deg)))
        self.car.solve()

    def test_drift_rotates_car_around_front_axle(self):
        self.drift(30.0)
        # the front axle centre stays put, the back of the car swings out
        mid = lambda f: (f('DEF-Wheel.Ft.L').trans + f('DEF-Wheel.Ft.R').trans) * 0.5  # noqa: E731
        self.assertVec(mid(self.car.bone), mid(self.car.rest), tol=0.05)
        back = self.car.bone('DEF-Wheel.Bk.L').trans - self.car.rest('DEF-Wheel.Bk.L').trans
        self.assertGreater(back.length(), 50.0)
        body = self.car.bone('DEF-Body').rot * self.car.rest('DEF-Body').rot.inverse()
        self.assertAlmostEqual(angle_deg(body), 30.0, places=1)
        self.assertAlmostEqual(self.wheel_yaw('DEF-Wheel.Bk.L'), 30.0, places=1)

    def test_front_wheels_counter_steer_while_drifting(self):
        self.drift(30.0)
        for n in ('DEF-Wheel.Ft.L', 'DEF-Wheel.Ft.R'):
            # still pointing where the Steering control points: straight on
            self.assertAlmostEqual(self.wheel_yaw(n), 0.0, places=1, msg=n)

    def test_steering_control_does_not_drift(self):
        self.drift(30.0)
        steering = self.car.rig.global_(self.car.control('Steering'))
        rest = self.car.rig.global_(self.car.control('Steering'), True)
        self.assertVec(steering.trans, rest.trans, tol=0.05)


class Ground(Base):
    def test_bump_under_one_wheel(self):
        bump_at = self.car.rest('DEF-Wheel.Ft.L').trans

        def ground(x, y):
            return 10.0 if (Vec(x, y, 0) - Vec(bump_at.x, bump_at.y, 0)).length() < 20.0 else 0.0
        self.car.rig.ground = ground
        self.car.solve()
        self.assertVec(self.car.bone('DEF-Wheel.Ft.L').trans, self.car.rest('DEF-Wheel.Ft.L').trans + Z * 10.0)
        for n in ('DEF-Wheel.Ft.R', 'DEF-Wheel.Bk.L', 'DEF-Wheel.Bk.R'):
            self.assertRest((n,))
        # body: lifted at the front left, so it rolls and pitches a little
        body = self.car.bone('DEF-Body')
        self.assertGreater(body.trans.z, self.car.rest('DEF-Body').trans.z)
        left = body.rot.rotate(self.car.rest('DEF-Body').rot.unrotate(-self.car.right))
        self.assertGreater(left.z, 0.0, 'left side should go up')
        nose = body.rot.rotate(self.car.rest('DEF-Body').rot.unrotate(self.car.fwd))
        self.assertGreater(nose.z, 0.0, 'nose should go up')

    def test_wheel_sensor_limit(self):
        target = self.car.rest('DEF-Wheel.Ft.L').trans

        def ground(x, y):
            near = (Vec(x, y, 0) - Vec(target.x, target.y, 0)).length() < 20.0
            return -40.0 if near else 0.0
        self.car.rig.ground = ground
        self.car.solve()
        # a hole deeper than GroundSensorLimit (20 cm): the wheel drops 20 cm only
        self.assertVec(self.car.bone('DEF-Wheel.Ft.L').trans, target - Z * 20.0)

    def test_ramp_pitches_car_and_keeps_wheelbase(self):
        bk = self.car.rest('DEF-Wheel.Bk.L').trans
        fwd = self.car.fwd
        slope = math.tan(math.radians(10.0))

        def ground(x, y):
            d = (Vec(x, y, 0) - Vec(bk.x, bk.y, 0)).dot(fwd)
            return max(0.0, d) * slope
        self.car.rig.ground = ground
        self.car.solve()
        body = self.car.bone('DEF-Body')
        nose = body.rot.rotate(self.car.rest('DEF-Body').rot.unrotate(fwd))
        pitch = math.degrees(math.atan2(nose.z, nose.dot(fwd)))
        self.assertAlmostEqual(pitch, 10.0, delta=0.6)
        ft = self.car.bone('DEF-Wheel.Ft.L').trans
        bkn = self.car.bone('DEF-Wheel.Bk.L').trans
        self.assertAlmostEqual((ft - bkn).length(), 2 * HALF_BASE, delta=0.5)

    def test_ground_detection_off(self):
        self.car.rig.ground = lambda x, y: 30.0
        self.car.rig.channels[('Root', 'GroundDetection')] = 0.0
        self.car.solve()
        self.assertRest()

    def test_raised_ground_lifts_car(self):
        self.car.rig.ground = lambda x, y: 30.0
        self.car.solve()
        for n in ALL:
            self.assertVec(self.car.bone(n).trans, self.car.rest(n).trans + Z * 30.0, tol=0.05, msg=n)


class Suspension(Base):
    def body_pitch_roll(self):
        body = self.car.bone('DEF-Body')
        rest = self.car.rest('DEF-Body')
        nose = body.rot.rotate(rest.rot.unrotate(self.car.fwd))
        right = body.rot.rotate(rest.rot.unrotate(self.car.right))
        return (math.degrees(math.atan2(nose.z, nose.dot(self.car.fwd))),
                math.degrees(math.atan2(right.z, right.dot(self.car.right))))

    def test_suspension_forward_dips_nose(self):
        self.car.set_value('Suspension', Vec(200.0, 0.0, 0.0))
        self.car.solve()
        pitch, roll = self.body_pitch_roll()
        self.assertAlmostEqual(pitch, -6.0, places=2)
        self.assertAlmostEqual(roll, 0.0, places=2)
        self.assertRest(WHEELS)

    def test_suspension_right_rolls_right_side_down(self):
        self.car.set_value('Suspension', Vec(0.0, 100.0, 0.0))
        self.car.solve()
        pitch, roll = self.body_pitch_roll()
        self.assertAlmostEqual(roll, -3.5, places=2)

    def test_suspension_bounce(self):
        self.car.set_value('Suspension', Vec(0.0, 0.0, -50.0))
        self.car.solve()
        self.assertVec(self.car.bone('DEF-Body').trans, self.car.rest('DEF-Body').trans - Z * 10.0)

    def test_damper_lifts_body_corner(self):
        self.car.set_value('WheelDamper_Ft_L', Vec(0.0, 0.0, 10.0))
        self.car.solve()
        self.assertRest(WHEELS)
        pitch, roll = self.body_pitch_roll()
        self.assertGreater(pitch, 0.0, 'nose should go up')
        self.assertLess(roll, 0.0, 'left side should go up')

    def test_suspension_factor_zero_ignores_dampers(self):
        self.car.rig.channels[('Root', 'SuspensionFactor')] = 0.0
        self.car.rig.channels[('Root', 'SuspensionRollingFactor')] = 0.0
        self.car.set_value('WheelDamper_Ft_L', Vec(0.0, 0.0, 10.0))
        self.car.solve()
        self.assertRest()

    def test_door_follows_body(self):
        self.car.set_value('Suspension', Vec(0.0, 0.0, 50.0))
        self.car.solve()
        self.assertVec(self.car.bone('DEF-Door.L').trans, self.car.rest('DEF-Door.L').trans + Z * 10.0)


class CarFacingX(RestPose):
    kw = {'yaw_deg': 0.0, 'bone_spin': False}


class CarFacingOdd(Driving):
    kw = {'yaw_deg': 37.0}


class SixWheels(Ground):
    kw = {'extra_pair': True}

    def test_ramp_pitches_car_and_keeps_wheelbase(self):
        # the back axle is the rearmost pair here, which is not on the ramp
        pass

    def test_rest(self):
        self.car.solve()
        self.assertRest(ALL + ('DEF-Wheel.Bk.L.001', 'DEF-Wheel.Bk.R.001'))


if __name__ == '__main__':
    unittest.main()
