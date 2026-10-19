# Repository Structure

> Describes the current layout; no production module was moved by this change.

```text
Ground_Station/
├── main.py                     screen → UAV command bridge (GroundStationLink V2)
├── components/
│   ├── screen_start_bridge.py  D-task START orchestration
│   ├── land_air_app.py         D-task read-only display + coordination
│   ├── fleet_app.py            generic FleetBus UI
│   ├── fleet_protocol.py       FleetBus V1 framing + CRC16
│   ├── protocol.py             GroundStationLink V2 + HMAC-SHA256
│   ├── half_duplex_master.py   half-duplex arbitration
│   ├── serial_transport.py     pyserial + BB 33 envelope
│   ├── ground_link.py          V2 link service
│   ├── fleet_store.py, state_store.py, trace_sync.py
│   ├── led_daemon.py, led_control.py
│   ├── ground_cue_player.py, buzzer_control.py
│   └── ui/                     PyQt5 windows and map widget
├── config/                     station.example.json, station.local.json (ignored),
│                               secrets/ (ignored)
├── task_config.json            screen action → command mapping
├── fleet_config.json           FleetBus display/timing/field policy
├── d_task_fleet_config.json    D-task parameters
├── deploy/                     installer + systemd/desktop templates
├── docs/                       architecture, configuration, testing, structure
├── tests/                      pytest suite + data fixtures
└── tools/                      auxiliary tools
```

## Entry points

| Purpose | Command |
|---|---|
| Screen command bridge (V2/HMAC) | `python3 main.py --config ...` |
| D-task display + coordination | `python3 components/land_air_app.py ...` |
| Generic FleetBus UI | `python3 components/fleet_app.py ...` |
| START workflow | `python3 components/screen_start_bridge.py ...` |
| LED daemon | `python3 components/led_daemon.py` (systemd) |

## Where to add code

- Protocol change → `components/fleet_protocol.py` (V1) or `components/protocol.py` (V2)
- New operator flow → `components/screen_start_bridge.py` + `task_config.json`
- UI → `components/ui/`
- Tests → `tests/`
- Deployment → `deploy/`

## Generated / ignored

`config/station.local.json`, `config/*.local.json`, `config/secrets/*`,
`__pycache__/`, `.pytest_cache/`, `.test_tmp/`, `tmp*`, `gstest-*` are ignored.
`config/secrets/.gitignore` is the only tracked file in that directory.

## Related documents

- `docs/SYSTEM_ARCHITECTURE.md`
- `docs/architecture.md` (data flow; its "all frames HMAC" wording applies only to V2)
- `docs/configuration.md`
- `docs/TESTING.md`
