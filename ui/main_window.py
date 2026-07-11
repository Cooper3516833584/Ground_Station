from __future__ import annotations

from PyQt5 import QtCore, QtWidgets

from models import MissionState
from state_store import StateStore


MODE_NAMES = {1: "HOLD ALT", 2: "HOLD POS", 3: "PROGRAM"}
MISSION_COLORS = {
    MissionState.IDLE: "#66717d",
    MissionState.ACQUIRING: "#0b7189",
    MissionState.READY: "#16805b",
    MissionState.COUNTDOWN: "#c67819",
    MissionState.RUNNING: "#2166c2",
    MissionState.STOPPING: "#b4232c",
    MissionState.LANDING: "#7a5ea8",
    MissionState.COMPLETED: "#16805b",
    MissionState.FAILED: "#b4232c",
}


class MainWindow(QtWidgets.QMainWindow):
    set_targets_requested = QtCore.pyqtSignal(int, int)
    command_requested = QtCore.pyqtSignal(object)

    def __init__(self, store: StateStore):
        super().__init__()
        self._store = store
        self._labels: dict[str, QtWidgets.QLabel] = {}
        self.setWindowTitle("Ground Station")
        self.setMinimumSize(1024, 600)
        self.resize(1024, 600)
        self._build()
        self._apply_stylesheet()
        self.refresh()

    def _build(self) -> None:
        root = QtWidgets.QWidget(objectName="root")
        self.setCentralWidget(root)
        page = QtWidgets.QVBoxLayout(root)
        page.setContentsMargins(0, 0, 0, 0)
        page.setSpacing(0)
        page.addWidget(self._build_header())

        body = QtWidgets.QWidget()
        body_layout = QtWidgets.QVBoxLayout(body)
        body_layout.setContentsMargins(14, 12, 14, 10)
        body_layout.setSpacing(10)

        panels = QtWidgets.QHBoxLayout()
        panels.setSpacing(10)
        panels.addWidget(self._build_telemetry_panel(), 5)
        panels.addWidget(self._build_mission_panel(), 7)
        body_layout.addLayout(panels, 1)
        page.addWidget(body, 1)

    def _build_header(self) -> QtWidgets.QFrame:
        header = QtWidgets.QFrame(objectName="header")
        header.setFixedHeight(76)
        layout = QtWidgets.QHBoxLayout(header)
        layout.setContentsMargins(18, 10, 18, 10)

        title_box = QtWidgets.QVBoxLayout()
        title = QtWidgets.QLabel("GROUND STATION", objectName="appTitle")
        subtitle = QtWidgets.QLabel("Flight telemetry and mission control", objectName="subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        layout.addLayout(title_box)
        layout.addStretch(1)

        link_box = QtWidgets.QHBoxLayout()
        link_box.setSpacing(8)
        self._labels["link_dot"] = QtWidgets.QLabel(objectName="linkDot")
        self._labels["link_dot"].setFixedSize(12, 12)
        self._labels["link"] = QtWidgets.QLabel("OFFLINE", objectName="linkText")
        link_box.addWidget(self._labels["link_dot"])
        link_box.addWidget(self._labels["link"])
        layout.addLayout(link_box)

        for key, caption in (("age", "PACKET AGE"), ("rate", "RX RATE"), ("session", "SESSION")):
            box = QtWidgets.QVBoxLayout()
            box.setSpacing(2)
            name = QtWidgets.QLabel(caption, objectName="headerCaption")
            value = QtWidgets.QLabel("--", objectName="headerValue")
            value.setMinimumWidth(92)
            value.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            self._labels[key] = value
            box.addWidget(name)
            box.addWidget(value)
            layout.addLayout(box)
        return header

    def _build_telemetry_panel(self) -> QtWidgets.QFrame:
        panel = self._panel("FLIGHT TELEMETRY", "Position and vehicle health")
        layout = panel.layout()
        position = QtWidgets.QHBoxLayout()
        position.setSpacing(8)
        position.addWidget(self._value_block("X POSITION", "pos_x", "m"))
        position.addWidget(self._value_block("Y POSITION", "pos_y", "m"))
        layout.addLayout(position)

        rows = QtWidgets.QGridLayout()
        rows.setHorizontalSpacing(14)
        rows.setVerticalSpacing(10)
        for row, (key, caption) in enumerate((
            ("battery", "Battery voltage"),
            ("mode", "Flight mode"),
            ("unlock", "Motor state"),
        )):
            rows.addWidget(QtWidgets.QLabel(caption, objectName="rowCaption"), row, 0)
            value = QtWidgets.QLabel("--", objectName="rowValue")
            value.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            self._labels[key] = value
            rows.addWidget(value, row, 1)
        rows.setColumnStretch(0, 1)
        layout.addLayout(rows)
        layout.addStretch(1)
        return panel

    def _build_mission_panel(self) -> QtWidgets.QFrame:
        panel = self._panel("CURRENT MISSION", "Aircraft-reported execution state")
        layout = panel.layout()
        self._labels["mission_state"] = QtWidgets.QLabel("IDLE", objectName="missionState")
        layout.addWidget(self._labels["mission_state"])
        self._labels["mission_message"] = QtWidgets.QLabel(
            "Waiting for mission status", objectName="missionMessage"
        )
        self._labels["mission_message"].setWordWrap(True)
        self._labels["mission_message"].setMinimumHeight(66)
        self._labels["mission_message"].setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        layout.addWidget(self._labels["mission_message"])
        layout.addStretch(1)
        return panel

    def _panel(self, title: str, subtitle: str) -> QtWidgets.QFrame:
        panel = QtWidgets.QFrame(objectName="panel")
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(16, 13, 16, 13)
        layout.setSpacing(8)
        layout.addWidget(QtWidgets.QLabel(title, objectName="panelTitle"))
        layout.addWidget(QtWidgets.QLabel(subtitle, objectName="panelSubtitle"))
        return panel

    def _value_block(self, caption: str, key: str, unit: str) -> QtWidgets.QFrame:
        block = QtWidgets.QFrame(objectName="valueBlock")
        layout = QtWidgets.QVBoxLayout(block)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(1)
        layout.addWidget(QtWidgets.QLabel(caption, objectName="valueCaption"))
        line = QtWidgets.QHBoxLayout()
        value = QtWidgets.QLabel("--", objectName="positionValue")
        self._labels[key] = value
        line.addWidget(value)
        line.addStretch(1)
        line.addWidget(QtWidgets.QLabel(unit, objectName="unit"))
        layout.addLayout(line)
        return block

    def refresh(self) -> None:
        telemetry = self._store.telemetry
        stale = self._store.is_stale()
        connected = self._store.link.connected
        if not connected:
            link_text, link_color = "OFFLINE", "#e15b64"
        elif stale:
            link_text, link_color = "DATA STALE", "#e7a33e"
        else:
            link_text, link_color = "ONLINE", "#49c28b"
        self._labels["link"].setText(link_text)
        self._labels["link_dot"].setStyleSheet(f"background: {link_color}; border-radius: 6px;")

        age = self._store.telemetry_age()
        self._labels["age"].setText("--" if age is None else f"{age:.1f} s")
        rate = self._store.link.telemetry_hz
        self._labels["rate"].setText("--" if rate <= 0 else f"{rate:.1f} Hz")
        session = self._store.link.session
        self._labels["session"].setText("--" if session is None else f"{session:08X}")

        if telemetry is None:
            for key in ("pos_x", "pos_y", "battery", "mode", "unlock"):
                self._labels[key].setText("--")
        else:
            self._labels["pos_x"].setText(f"{telemetry.pos_x_m:.2f}")
            self._labels["pos_y"].setText(f"{telemetry.pos_y_m:.2f}")
            self._labels["battery"].setText(f"{telemetry.battery_v:.2f} V")
            self._labels["mode"].setText(MODE_NAMES.get(telemetry.mode, f"MODE {telemetry.mode}"))
            self._labels["unlock"].setText("ARMED" if telemetry.unlock else "LOCKED")
            self._labels["unlock"].setStyleSheet(
                f"color: {'#b4232c' if telemetry.unlock else '#16805b'}; font-weight: 700;"
            )
        mission = self._store.mission
        color = MISSION_COLORS.get(mission.state, "#66717d")
        self._labels["mission_state"].setText(mission.state.name)
        self._labels["mission_state"].setStyleSheet(f"color: {color};")
        fallback = "Waiting for mission status" if mission.state == MissionState.IDLE else "Aircraft has not provided a status message"
        self._labels["mission_message"].setText(mission.message or fallback)

    def _apply_stylesheet(self) -> None:
        self.setStyleSheet("""
            * { font-family: "DejaVu Sans", "Segoe UI", sans-serif; }
            QWidget#root { background: #f2f4f6; color: #20252b; }
            QFrame#header { background: #20252b; color: white; }
            QLabel#appTitle { color: white; font-size: 21px; font-weight: 700; }
            QLabel#subtitle { color: #aeb7c1; font-size: 11px; }
            QLabel#linkText { color: white; font-size: 14px; font-weight: 700; margin-right: 16px; }
            QLabel#headerCaption { color: #8995a1; font-size: 9px; }
            QLabel#headerValue { color: white; font-size: 14px; font-weight: 600; }
            QFrame#panel { background: white; border: 1px solid #d8dde3; border-radius: 6px; }
            QLabel#panelTitle { color: #20252b; font-size: 16px; font-weight: 700; }
            QLabel#panelSubtitle { color: #74808b; font-size: 11px; }
            QFrame#valueBlock { background: #eef2f5; border-radius: 4px; }
            QLabel#valueCaption { color: #74808b; font-size: 9px; font-weight: 600; }
            QLabel#positionValue { color: #0b7189; font-size: 29px; font-weight: 700; }
            QLabel#unit { color: #74808b; font-size: 12px; }
            QLabel#rowCaption { color: #59636d; font-size: 12px; }
            QLabel#rowValue { color: #20252b; font-size: 16px; font-weight: 600; }
            QLabel#missionState { font-size: 18px; font-weight: 800; }
            QLabel#missionMessage { color: #20252b; font-size: 25px; font-weight: 600; }
        """)
