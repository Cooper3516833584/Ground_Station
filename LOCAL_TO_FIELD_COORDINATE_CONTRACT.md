# Local-to-FIELD Coordinate Contract

This repository is the sole FIELD-coordinate authority for the three-end
D-task integration. Phase 01 adds documentation only; it does not change the
FleetBus wire protocol, UI, Store, radio, or command paths.

Each drone and car `REPORT` carries a pose in that device's startup-local
frame. `x_cm`, `y_cm`, and `z_cm` are centimetres; `heading_cdeg` is
counter-clockwise positive from `0..35999`; and velocity is in the same local
frame in centimetres per second. The ground station alone converts local pose
to FIELD for maps and converts FIELD targets back to local before sending a
device command.

FIELD is lower-left-origin, `+X` right, `+Y` upward, centimetres, and
counter-clockwise-positive. Each node configuration will contain
`origin_world_x_cm`, `origin_world_y_cm`, and
`local_x_heading_world_deg`. For
`theta = radians(local_x_heading_world_deg)`:

```text
local -> FIELD:
xw = ox + cos(theta)*xl - sin(theta)*yl
yw = oy + sin(theta)*xl + cos(theta)*yl
hw = (hl + local_x_heading_world_deg) % 360

FIELD -> local:
dx = xw - ox; dy = yw - oy
xl =  cos(theta)*dx + sin(theta)*dy
yl = -sin(theta)*dx + cos(theta)*dy
hl = (hw - local_x_heading_world_deg) % 360
```

Velocities rotate but never translate. FIELD width/height are configuration,
not scattered algorithm constants. `400 x 500 cm` is only a provisional
D-task extent; field landmarks and each device's initial pose require an
approved plan or measurement.

`world_pose` denotes a device localization reference point, not a display
centre: the car is its rear axle and the drone remains its Navigation reference.
UI offsets (the car body centre defaults to `7.125 cm` forward) cannot enter
the transform or outgoing target coordinates.

The current `FleetStore` directly copies `REPORT.x_cm/y_cm` into
`world_pose`. That is the identified Phase-02 change point; this phase
intentionally leaves it untouched. Existing FleetBus frame layout, CRC, node
IDs, polling, retries and disaster-survey functions remain protected. No
secret, HMAC, or password belongs in JSON configuration.
