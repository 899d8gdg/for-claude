"""Sequencer baking, like Rigacar's "Bake wheels rotation" and "Bake car steering".

Animate the Root (and Drift) in a Level Sequence, then bake: the wheels get
keys on their Wheel_*_rotation channels so they roll exactly as far as they
travel, and the Steering control gets keys on Steering_rotation so the front
wheels point along the path.
"""

import math

from .xmath import Vec, Quat, X, Y

STEERING_CHANNEL = 'Steering_rotation'


# ---------------------------------------------------------------------------
# the maths (no Unreal calls)

def wheel_angles(positions, forwards, radius, brakes=None):
    """Accumulated wheel rotation (radians) for each frame.

    positions: wheel centre per frame; forwards: the wheel's forward direction
    per frame; brakes: 0 (free) to 1 (locked) per frame.
    """
    angles = [0.0]
    total = 0.0
    for i in range(1, len(positions)):
        v = Vec(*positions[i]) - Vec(*positions[i - 1])
        speed = math.copysign(v.length(), Vec(*forwards[i]).dot(v))
        if brakes:
            speed *= 1.0 - min(max(brakes[i], 0.0), 1.0)
        total += speed / radius
        angles.append(total)
    return angles


def steering_offsets(positions, rotations, distance, factor=1.0, min_move=0.01):
    """Sideways Steering offset (cm) per frame so the front wheels follow the path.

    positions / rotations: the front axle per frame. distance: how far ahead of
    the front axle the Steering control sits.
    """
    n = len(positions)
    out = [0.0] * n
    last = 0.0
    for i in range(n):
        j = i + 1 if i + 1 < n else i
        k = i if i + 1 < n else i - 1
        if k < 0:
            break
        v = Vec(*positions[j]) - Vec(*positions[k])
        q = Quat(*rotations[i])
        fwd = q.rotate(X)
        right = q.rotate(Y)
        ahead = v.dot(fwd)
        if v.length() > min_move and abs(ahead) > 1e-6:
            last = (v * (distance * factor / ahead)).dot(right)
        out[i] = last
    return out


# ---------------------------------------------------------------------------
# Unreal side

class BakeError(Exception):
    pass


def _ue():
    import unreal
    return unreal


def _car_rigs(seq):
    u = _ue()
    out = []
    for proxy in u.ControlRigSequencerLibrary.get_control_rigs(seq):
        rig = proxy.control_rig
        if rig is None:
            continue
        names = [str(k.name) for k in rig.get_hierarchy().get_controls()]
        if 'Root' in names and 'Steering' in names and any(n.startswith('GroundSensor_') for n in names):
            out.append((proxy, rig, names))
    return out


def _channel(names, wanted):
    for n in names:
        if n == wanted:
            return n
    for n in names:
        if n.endswith(wanted):
            return n
    return None


def _frames(seq, start, end):
    u = _ue()
    if start is None:
        start = seq.get_playback_start()
    if end is None:
        end = seq.get_playback_end()
    if end - start < 2:
        raise BakeError('The playback range is too short to bake.')
    return [u.FrameNumber(f) for f in range(start, end)]


def _clear_channels(proxy, channel_names):
    """Remove existing keys on the given channels of the Control Rig track."""
    track = proxy.track
    if track is None:
        return
    wanted = set(channel_names)
    for section in track.get_sections():
        for ch in section.get_all_channels():
            name = str(ch.channel_name)
            if name in wanted or any(name.endswith(w) for w in wanted):
                for key in list(ch.get_keys()):
                    ch.remove_key(key)


def _current_sequence():
    u = _ue()
    seq = u.LevelSequenceEditorBlueprintLibrary.get_current_level_sequence()
    if seq is None:
        raise BakeError('Open the Level Sequence that animates the car first.')
    rigs = _car_rigs(seq)
    if not rigs:
        raise BakeError('The open Level Sequence has no car Control Rig track. '
                        'Add the car to the sequence and give it a Control Rig track with the CR_ asset.')
    return seq, rigs


def _world(seq, rig, control, frames):
    u = _ue()
    return list(u.ControlRigSequencerLibrary.get_control_rig_world_transforms(seq, rig, control, frames))


def bake_wheels(start=None, end=None):
    u = _ue()
    seq, rigs = _current_sequence()
    frames = _frames(seq, start, end)
    lib = u.ControlRigSequencerLibrary
    report = []
    for proxy, rig, names in rigs:
        h = rig.get_hierarchy()
        sensors = [n for n in names if n.startswith('GroundSensor_') and not n.startswith('GroundSensor_Axle')]
        root_z = h.get_global_transform(u.RigElementKey(u.RigElementType.CONTROL, 'Root'), True).translation.z
        targets = {}
        for sensor in sensors:
            suffix = sensor[len('GroundSensor_'):]
            ch = _channel(names, 'Wheel_%s_rotation' % suffix)
            if ch:
                targets[sensor] = (suffix, ch)
        _clear_channels(proxy, [ch for _, ch in targets.values()])
        on_x = _channel(names, 'WheelsOnXAxis')
        if on_x and lib.get_local_control_rig_float(seq, rig, on_x, frames[0]) > 0.0:
            u.log_warning('Car Rig: WheelsOnXAxis is on, so the wheels will spin twice as much. '
                          'Set it to 0 after baking.')
        with u.ScopedSlowTask(len(targets), 'Baking wheel rotation') as task:
            task.make_dialog(True)
            for sensor, (suffix, ch) in targets.items():
                if task.should_cancel():
                    raise BakeError('Cancelled.')
                task.enter_progress_frame(1, sensor)
                rest = h.get_global_transform(u.RigElementKey(u.RigElementType.CONTROL, sensor), True)
                xfs = _world(seq, rig, sensor, frames)
                scale = abs(xfs[0].scale3d.x) or 1.0
                radius = max(rest.translation.z - root_z, 1.0) * scale
                positions = [Vec(t.translation.x, t.translation.y, t.translation.z) for t in xfs]
                forwards = [Quat(t.rotation.x, t.rotation.y, t.rotation.z, t.rotation.w).rotate(X) for t in xfs]
                brake_name = _channel(names, 'WheelBrake_%s' % suffix.split('_')[0])
                brakes = None
                if brake_name:
                    brakes = [lib.get_local_control_rig_float(seq, rig, brake_name, f) for f in frames]
                for f, a in zip(frames, wheel_angles(positions, forwards, radius, brakes)):
                    lib.set_local_control_rig_float(seq, rig, ch, f, a)
        report.append('%s: %d wheels over %d frames' % (rig.get_name(), len(targets), len(frames)))
    return report


def bake_steering(start=None, end=None, factor=1.0):
    u = _ue()
    seq, rigs = _current_sequence()
    frames = _frames(seq, start, end)
    lib = u.ControlRigSequencerLibrary
    report = []
    for proxy, rig, names in rigs:
        h = rig.get_hierarchy()
        ch = _channel(names, STEERING_CHANNEL)
        if not ch:
            continue
        _clear_channels(proxy, [ch])
        steering = h.get_global_transform(u.RigElementKey(u.RigElementType.CONTROL, 'Steering'), True).translation
        axle = h.get_global_transform(u.RigElementKey(u.RigElementType.CONTROL, 'GroundSensor_Axle_Ft'),
                                      True).translation
        distance = (Vec(steering.x, steering.y, steering.z) - Vec(axle.x, axle.y, axle.z)).length()
        xfs = _world(seq, rig, 'GroundSensor_Axle_Ft', frames)
        scale = abs(xfs[0].scale3d.x) or 1.0
        positions = [Vec(t.translation.x, t.translation.y, t.translation.z) for t in xfs]
        rotations = [Quat(t.rotation.x, t.rotation.y, t.rotation.z, t.rotation.w) for t in xfs]
        offsets = steering_offsets(positions, rotations, distance * scale, factor)
        for f, s in zip(frames, offsets):
            lib.set_local_control_rig_float(seq, rig, ch, f, s / scale)
        report.append('%s: steering over %d frames' % (rig.get_name(), len(frames)))
    return report


def clear_baked(wheels=True, steering=True):
    seq, rigs = _current_sequence()
    for proxy, rig, names in rigs:
        chans = []
        if wheels:
            chans += [n for n in names if n.startswith('Wheel_') and n.endswith('_rotation')]
        if steering:
            chans += [n for n in names if n.endswith(STEERING_CHANNEL)]
        _clear_channels(proxy, chans)
    return ['Cleared baked keys on %d rigs' % len(rigs)]
