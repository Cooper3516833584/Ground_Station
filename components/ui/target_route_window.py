from __future__ import annotations

import math

from PyQt5 import QtCore, QtGui, QtWidgets

from target_routes import (
    FACE_X,
    LANDING_POINT,
    SHELVES,
    START_POINT,
    WAREHOUSE_HEIGHT_CM,
    WAREHOUSE_WIDTH_CM,
    MissionRoute,
    all_routes,
    route_for,
)


OUTBOUND_COLOR = QtGui.QColor("#ffb454")
RETURN_COLOR = QtGui.QColor("#36d6c7")


class WarehouseMap(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._route = route_for("A1")
        self._show_all = True
        self._aircraft_progress: float | None = None
        self.setMinimumSize(500, 360)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

    def set_target(self, code: str) -> None:
        self._route = route_for(code)
        self._aircraft_progress = None
        self.update()

    def set_show_all(self, enabled: bool) -> None:
        self._show_all = enabled
        self.update()

    def set_aircraft_progress(self, progress: float | None) -> None:
        self._aircraft_progress = progress
        self.update()

    def _map_rect(self) -> QtCore.QRectF:
        available = self.rect().adjusted(48, 30, -30, -38)
        ratio = WAREHOUSE_WIDTH_CM / WAREHOUSE_HEIGHT_CM
        width = min(available.width(), available.height() * ratio)
        height = width / ratio
        if height > available.height():
            height = available.height()
            width = height * ratio
        left = available.left() + (available.width() - width) / 2
        top = available.top() + (available.height() - height) / 2
        return QtCore.QRectF(left, top, width, height)

    def _to_screen(self, point: tuple[float, float]) -> QtCore.QPointF:
        area = self._map_rect()
        x, y = point
        return QtCore.QPointF(
            area.left() + x / WAREHOUSE_WIDTH_CM * area.width(),
            area.bottom() - y / WAREHOUSE_HEIGHT_CM * area.height(),
        )

    def paintEvent(self, _event) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.fillRect(self.rect(), QtGui.QColor("#091722"))
        self._draw_grid(painter)
        self._draw_warehouse(painter)
        if self._show_all:
            self._draw_all_routes(painter)
        self._draw_route(painter, self._route)
        self._draw_target(painter)
        self._draw_aircraft(painter)
        self._draw_compass(painter)

    def _draw_grid(self, painter: QtGui.QPainter) -> None:
        area = self._map_rect()
        painter.save()
        painter.setClipRect(area)
        for value in range(0, 501, 50):
            alpha = 35 if value % 100 else 65
            painter.setPen(QtGui.QPen(QtGui.QColor(91, 129, 150, alpha), 1))
            if value <= 500:
                x = self._to_screen((float(value), 0.0)).x()
                painter.drawLine(QtCore.QPointF(x, area.top()), QtCore.QPointF(x, area.bottom()))
            if value <= 400:
                y = self._to_screen((0.0, float(value))).y()
                painter.drawLine(QtCore.QPointF(area.left(), y), QtCore.QPointF(area.right(), y))
        painter.restore()

    def _draw_warehouse(self, painter: QtGui.QPainter) -> None:
        area = self._map_rect()
        painter.setPen(QtGui.QPen(QtGui.QColor("#537184"), 1.5))
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawRoundedRect(area, 3, 3)

        painter.setFont(QtGui.QFont("Microsoft YaHei", 8))
        painter.setPen(QtGui.QColor("#6f8b9c"))
        painter.drawText(
            QtCore.QRectF(area.left(), area.bottom() + 8, area.width(), 20),
            QtCore.Qt.AlignCenter,
            "500 cm",
        )
        painter.save()
        painter.translate(area.left() - 27, area.center().y())
        painter.rotate(-90)
        painter.drawText(QtCore.QRectF(-50, -10, 100, 20), QtCore.Qt.AlignCenter, "400 cm")
        painter.restore()

        for shelf_index, (x, y1, y2) in enumerate(SHELVES):
            p1, p2 = self._to_screen((x, y1)), self._to_screen((x, y2))
            painter.setPen(QtGui.QPen(QtGui.QColor("#dbe8ee"), 5, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
            painter.drawLine(p1, p2)
            painter.setPen(QtGui.QPen(QtGui.QColor("#ff4f5e"), 7, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
            painter.drawPoint(p1)
            painter.drawPoint(p2)
            left_face, right_face = (("A", "B") if shelf_index == 0 else ("C", "D"))
            self._draw_face_label(painter, left_face, x - 27, 205)
            self._draw_face_label(painter, right_face, x + 27, 205)

        self._draw_pad(painter, START_POINT, "起飞", False)
        self._draw_pad(painter, LANDING_POINT, "降落", True)
        self._draw_position_ticks(painter)

    def _draw_face_label(self, painter: QtGui.QPainter, face: str, x: float, y: float) -> None:
        point = self._to_screen((x, y))
        painter.setFont(QtGui.QFont("Microsoft YaHei", 10, QtGui.QFont.Bold))
        painter.setPen(QtGui.QColor("#aec2cd"))
        painter.drawText(QtCore.QRectF(point.x() - 18, point.y() - 10, 36, 20), QtCore.Qt.AlignCenter, face)

    def _draw_pad(self, painter: QtGui.QPainter, point, label: str, landing: bool) -> None:
        center = self._to_screen(point)
        scale = self._map_rect().width() / WAREHOUSE_WIDTH_CM
        size = 50 * scale
        color = QtGui.QColor("#36d6c7") if landing else QtGui.QColor("#ffb454")
        painter.setPen(QtGui.QPen(color, 2))
        painter.setBrush(QtGui.QColor(color.red(), color.green(), color.blue(), 30))
        if landing:
            painter.drawEllipse(center, size / 2, size / 2)
        else:
            painter.drawRoundedRect(
                QtCore.QRectF(center.x() - size / 2, center.y() - size / 2, size, size), 4, 4
            )
        painter.setFont(QtGui.QFont("Microsoft YaHei", 8, QtGui.QFont.Bold))
        painter.setPen(color)
        painter.drawText(
            QtCore.QRectF(center.x() - 35, center.y() + size / 2 + 3, 70, 18),
            QtCore.Qt.AlignCenter,
            label,
        )

    def _draw_position_ticks(self, painter: QtGui.QPainter) -> None:
        painter.setFont(QtGui.QFont("Segoe UI", 7))
        for face, x in FACE_X.items():
            if face in ("A", "C"):
                labels = (("1/4", 250.0), ("2/5", 200.0), ("3/6", 150.0))
            else:
                labels = (("1/4", 150.0), ("2/5", 200.0), ("3/6", 250.0))
            for label, y in labels:
                point = self._to_screen((x, y))
                painter.setPen(QtGui.QPen(QtGui.QColor("#466273"), 1))
                painter.setBrush(QtGui.QColor("#122b3a"))
                painter.drawEllipse(point, 4, 4)
                if face in ("A", "C"):
                    rect = QtCore.QRectF(point.x() - 31, point.y() - 8, 24, 16)
                else:
                    rect = QtCore.QRectF(point.x() + 7, point.y() - 8, 24, 16)
                painter.setPen(QtGui.QColor("#688696"))
                painter.drawText(rect, QtCore.Qt.AlignCenter, label)

    def _draw_all_routes(self, painter: QtGui.QPainter) -> None:
        seen: set[tuple[str, int]] = set()
        pen = QtGui.QPen(QtGui.QColor(73, 126, 146, 35), 1)
        for route in all_routes():
            key = (route.target.face, route.target.column)
            if key in seen:
                continue
            seen.add(key)
            self._draw_polyline(painter, route.outbound, pen, arrows=False)
            self._draw_polyline(painter, route.return_path, pen, arrows=False)

    def _draw_route(self, painter: QtGui.QPainter, route: MissionRoute) -> None:
        self._draw_polyline(
            painter,
            route.outbound,
            QtGui.QPen(OUTBOUND_COLOR, 3, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin),
            arrows=True,
            offset=QtCore.QPointF(-1.5, -1.5),
        )
        self._draw_polyline(
            painter,
            route.return_path,
            QtGui.QPen(RETURN_COLOR, 3, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin),
            arrows=True,
            offset=QtCore.QPointF(1.5, 1.5),
        )

    def _draw_polyline(
        self,
        painter,
        points,
        pen,
        arrows: bool,
        offset: QtCore.QPointF = QtCore.QPointF(),
    ) -> None:
        screen_points = [self._to_screen(point) + offset for point in points]
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawPolyline(QtGui.QPolygonF(screen_points))
        if arrows:
            for start, end in zip(screen_points, screen_points[1:]):
                if QtCore.QLineF(start, end).length() > 22:
                    self._draw_arrow(painter, start, end, pen.color())

    @staticmethod
    def _draw_arrow(painter, start, end, color) -> None:
        line = QtCore.QLineF(start, end)
        center = line.pointAt(0.58)
        angle = math.atan2(end.y() - start.y(), end.x() - start.x())
        length = 7.0
        wing = 4.0
        tip = QtCore.QPointF(center.x() + math.cos(angle) * length, center.y() + math.sin(angle) * length)
        left = QtCore.QPointF(center.x() - math.cos(angle) * 2 - math.sin(angle) * wing, center.y() - math.sin(angle) * 2 + math.cos(angle) * wing)
        right = QtCore.QPointF(center.x() - math.cos(angle) * 2 + math.sin(angle) * wing, center.y() - math.sin(angle) * 2 - math.cos(angle) * wing)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(color)
        painter.drawPolygon(QtGui.QPolygonF([tip, left, right]))

    def _draw_target(self, painter: QtGui.QPainter) -> None:
        target = self._route.target
        point = self._to_screen(target.point)
        painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 2))
        painter.setBrush(QtGui.QColor("#ff5f6d"))
        painter.drawEllipse(point, 7, 7)
        rect = QtCore.QRectF(point.x() - 23, point.y() - 31, 46, 20)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor("#ff5f6d"))
        painter.drawRoundedRect(rect, 6, 6)
        painter.setPen(QtGui.QColor("white"))
        painter.setFont(QtGui.QFont("Segoe UI", 9, QtGui.QFont.Bold))
        painter.drawText(rect, QtCore.Qt.AlignCenter, target.code)

    def _draw_aircraft(self, painter: QtGui.QPainter) -> None:
        if self._aircraft_progress is None:
            return
        points = self._route.display_points
        screen = [self._to_screen(point) for point in points]
        lengths = [QtCore.QLineF(a, b).length() for a, b in zip(screen, screen[1:])]
        total = sum(lengths)
        distance = min(max(self._aircraft_progress, 0.0), 1.0) * total
        position = screen[-1]
        for start, end, length in zip(screen, screen[1:], lengths):
            if distance <= length:
                position = QtCore.QLineF(start, end).pointAt(0 if length == 0 else distance / length)
                break
            distance -= length
        painter.setPen(QtGui.QPen(QtGui.QColor("white"), 2))
        painter.setBrush(QtGui.QColor("#246b8b"))
        painter.drawEllipse(position, 8, 8)
        painter.drawLine(position + QtCore.QPointF(-12, 0), position + QtCore.QPointF(12, 0))
        painter.drawLine(position + QtCore.QPointF(0, -12), position + QtCore.QPointF(0, 12))

    def _draw_compass(self, painter: QtGui.QPainter) -> None:
        area = self._map_rect()
        origin = QtCore.QPointF(area.left() + 22, area.top() + 38)
        painter.setPen(QtGui.QPen(QtGui.QColor("#ff5f6d"), 2))
        painter.drawLine(origin, origin + QtCore.QPointF(0, -22))
        self._draw_arrow(painter, origin, origin + QtCore.QPointF(0, -22), QtGui.QColor("#ff5f6d"))
        painter.setPen(QtGui.QColor("#ff7c86"))
        painter.setFont(QtGui.QFont("Segoe UI", 8, QtGui.QFont.Bold))
        painter.drawText(QtCore.QRectF(origin.x() - 13, origin.y() - 40, 26, 16), QtCore.Qt.AlignCenter, "Y+")


class TargetRouteWindow(QtWidgets.QMainWindow):
    def __init__(self, initial_target: str = "A1"):
        super().__init__()
        self._target_code = route_for(initial_target).target.code
        self._target_buttons: dict[str, QtWidgets.QPushButton] = {}
        self._face_buttons: dict[str, QtWidgets.QPushButton] = {}
        self._animation_value = 0
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(32)
        self._timer.timeout.connect(self._advance_animation)
        self.setWindowTitle("定向盘点航线 · 任务二")
        self.setMinimumSize(800, 480)
        self.resize(1024, 600)
        self._build()
        self._apply_stylesheet()
        self.select_target(self._target_code)

    def _build(self) -> None:
        root = QtWidgets.QWidget(objectName="root")
        self.setCentralWidget(root)
        page = QtWidgets.QVBoxLayout(root)
        page.setContentsMargins(0, 0, 0, 0)
        page.setSpacing(0)
        page.addWidget(self._build_header())

        content = QtWidgets.QHBoxLayout()
        content.setContentsMargins(14, 12, 14, 14)
        content.setSpacing(12)
        content.addWidget(self._build_map_panel(), 7)
        content.addWidget(self._build_control_panel(), 3)
        page.addLayout(content, 1)

    def _build_header(self) -> QtWidgets.QFrame:
        header = QtWidgets.QFrame(objectName="header")
        header.setFixedHeight(58)
        layout = QtWidgets.QHBoxLayout(header)
        layout.setContentsMargins(18, 8, 18, 8)

        title_box = QtWidgets.QVBoxLayout()
        title_box.setSpacing(1)
        title_box.addWidget(QtWidgets.QLabel("立体货架盘点无人机", objectName="appTitle"))
        title_box.addWidget(QtWidgets.QLabel("任务二 · 指定货物定向盘点航线", objectName="subtitle"))
        layout.addLayout(title_box)
        layout.addStretch(1)

        mode = QtWidgets.QLabel("UI 航线预览", objectName="previewChip")
        mode.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(mode)
        self._header_target = QtWidgets.QLabel("A1", objectName="headerTarget")
        self._header_target.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self._header_target)
        return header

    def _build_map_panel(self) -> QtWidgets.QFrame:
        panel = QtWidgets.QFrame(objectName="mapPanel")
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        title_line = QtWidgets.QHBoxLayout()
        title_line.addWidget(QtWidgets.QLabel("定点盘点航线图", objectName="panelTitle"))
        title_line.addStretch(1)
        title_line.addWidget(self._legend_dot(OUTBOUND_COLOR, "前往目标"))
        title_line.addWidget(self._legend_dot(RETURN_COLOR, "前往降落点"))
        layout.addLayout(title_line)
        self.map = WarehouseMap()
        layout.addWidget(self.map, 1)
        return panel

    def _build_control_panel(self) -> QtWidgets.QFrame:
        panel = QtWidgets.QFrame(objectName="controlPanel")
        panel.setMaximumWidth(320)
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(5)

        layout.addWidget(QtWidgets.QLabel("目标货位", objectName="panelTitle"))
        layout.addWidget(QtWidgets.QLabel("每面按左→右、上→下排列", objectName="hint"))

        face_row = QtWidgets.QHBoxLayout()
        face_row.setSpacing(5)
        for face in "ABCD":
            button = QtWidgets.QPushButton(face, objectName="faceButton")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked, value=face: self.select_target(value + "1"))
            self._face_buttons[face] = button
            face_row.addWidget(button)
        layout.addLayout(face_row)

        target_grid = QtWidgets.QGridLayout()
        target_grid.setSpacing(5)
        for index in range(1, 7):
            button = QtWidgets.QPushButton(str(index), objectName="targetButton")
            button.setCheckable(True)
            button.setMinimumHeight(29)
            button.clicked.connect(lambda _checked, value=index: self.select_target(self._target_code[0] + str(value)))
            self._target_buttons[str(index)] = button
            target_grid.addWidget(button, (index - 1) // 3, (index - 1) % 3)
        layout.addLayout(target_grid)

        self._summary = QtWidgets.QFrame(objectName="summary")
        summary_layout = QtWidgets.QVBoxLayout(self._summary)
        summary_layout.setContentsMargins(10, 6, 10, 6)
        summary_layout.setSpacing(1)
        self._summary_code = QtWidgets.QLabel("A1", objectName="summaryCode")
        self._summary_face = QtWidgets.QLabel("左侧货架 · A 面", objectName="summaryText")
        self._summary_height = QtWidgets.QLabel("上排 · 目标高度 140 cm", objectName="summaryText")
        summary_layout.addWidget(self._summary_code)
        summary_layout.addWidget(self._summary_face)
        summary_layout.addWidget(self._summary_height)
        layout.addWidget(self._summary)

        layout.addWidget(QtWidgets.QLabel("飞行顺序", objectName="sectionTitle"))
        self._steps = QtWidgets.QLabel(objectName="steps")
        self._steps.setWordWrap(True)
        layout.addWidget(self._steps)

        self._show_all = QtWidgets.QCheckBox("显示全部航线参考")
        self._show_all.setChecked(True)
        self._show_all.toggled.connect(self.map.set_show_all)
        layout.addWidget(self._show_all)
        layout.addStretch(1)

        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(6)
        self._play = QtWidgets.QPushButton("路线演示", objectName="primaryButton")
        self._play.clicked.connect(self.toggle_animation)
        reset = QtWidgets.QPushButton("复位", objectName="secondaryButton")
        reset.clicked.connect(self.reset_animation)
        buttons.addWidget(self._play, 1)
        buttons.addWidget(reset)
        layout.addLayout(buttons)

        self._status = QtWidgets.QLabel("航线已规划 · 未连接飞控", objectName="status")
        self._status.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self._status)
        return panel

    @staticmethod
    def _legend_dot(color: QtGui.QColor, text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(f"●  {text}", objectName="legend")
        label.setStyleSheet(f"color: {color.name()};")
        return label

    @QtCore.pyqtSlot(str)
    def select_target(self, code: str) -> None:
        route = route_for(code)
        self._target_code = route.target.code
        self._timer.stop()
        self._animation_value = 0
        self.map.set_target(self._target_code)
        self._play.setText("路线演示")
        self._header_target.setText(self._target_code)

        for face, button in self._face_buttons.items():
            button.setChecked(face == route.target.face)
        for index, button in self._target_buttons.items():
            button.setText(f"{route.target.face}{index}")
            button.setChecked(int(index) == route.target.index)

        self._summary_code.setText(route.target.code)
        self._summary_face.setText(route.target.face_name)
        self._summary_height.setText(
            f"{route.target.row_name} · 目标高度 {route.target.height_cm:.0f} cm"
        )
        self._steps.setText(
            "1  起飞点垂直起飞至 150 cm\n"
            "2  沿 Y+ 绕至货架北端\n"
            f"3  转入 {route.target.face} 面，沿 Y- 到达 {route.target.code}\n"
            "4  沿 Y+ 离开，前往降落点"
        )
        self._status.setText("航线已规划 · 未连接飞控")

    @QtCore.pyqtSlot()
    def toggle_animation(self) -> None:
        if self._timer.isActive():
            self._timer.stop()
            self._play.setText("继续演示")
            self._status.setText("路线演示已暂停")
            return
        if self._animation_value >= 1000:
            self._animation_value = 0
        self._timer.start()
        self._play.setText("暂停")
        self._status.setText("正在演示规划航线")

    @QtCore.pyqtSlot()
    def reset_animation(self) -> None:
        self._timer.stop()
        self._animation_value = 0
        self.map.set_aircraft_progress(None)
        self._play.setText("路线演示")
        self._status.setText("航线已规划 · 未连接飞控")

    def set_cycle_status(
        self,
        round_number: int,
        round_count: int,
        position: int,
        interval_seconds: float,
    ) -> None:
        self._status.setText(
            f"自动轮播 {round_number}/{round_count} · 航线 {position}/24 · "
            f"每条 {interval_seconds:g} 秒"
        )

    def set_cycle_complete(self, round_count: int) -> None:
        self._status.setText(
            f"自动轮播完成 · 共 {round_count} 轮 / {round_count * 24} 条次"
        )

    def show_scanning(self) -> None:
        self.reset_animation()
        self._header_target.setText("识别中")
        self._status.setText("摄像头 0 正在扫描目标二维码 · SSH 输入 quit 可取消")

    def set_operational_status(self, text: str) -> None:
        self._status.setText(text)

    @QtCore.pyqtSlot()
    def _advance_animation(self) -> None:
        self._animation_value += 6
        if self._animation_value >= 1000:
            self._animation_value = 1000
            self._timer.stop()
            self._play.setText("重新演示")
            self._status.setText("路线演示完成 · 已到达降落点")
        self.map.set_aircraft_progress(self._animation_value / 1000.0)

    def _apply_stylesheet(self) -> None:
        self.setStyleSheet("""
            * { font-family: "Microsoft YaHei", "Noto Sans CJK SC", "Segoe UI", sans-serif; }
            QWidget#root { background: #07111a; color: #e6f0f4; }
            QFrame#header { background: #102433; border-bottom: 1px solid #294657; }
            QLabel#appTitle { color: #f4f8fa; font-size: 19px; font-weight: 700; }
            QLabel#subtitle { color: #7f9baa; font-size: 11px; }
            QLabel#previewChip {
                color: #8de8de; background: #143c42; border: 1px solid #28666a;
                border-radius: 11px; padding: 4px 10px; font-size: 10px; font-weight: 600;
            }
            QLabel#headerTarget {
                color: #07111a; background: #ffb454; border-radius: 5px;
                min-width: 48px; padding: 6px 9px; margin-left: 8px;
                font-size: 17px; font-weight: 800;
            }
            QFrame#mapPanel, QFrame#controlPanel {
                background: #0d1d29; border: 1px solid #294657; border-radius: 8px;
            }
            QLabel#panelTitle { color: #edf5f7; font-size: 15px; font-weight: 700; }
            QLabel#hint { color: #7894a4; font-size: 10px; }
            QLabel#legend { font-size: 10px; font-weight: 600; margin-left: 12px; }
            QPushButton#faceButton, QPushButton#targetButton {
                color: #9ab1bd; background: #132936; border: 1px solid #2c4c5d;
                border-radius: 5px; padding: 6px; font-size: 12px; font-weight: 700;
            }
            QPushButton#faceButton:checked, QPushButton#targetButton:checked {
                color: #07111a; background: #ffb454; border-color: #ffb454;
            }
            QPushButton#faceButton:hover, QPushButton#targetButton:hover { border-color: #6b95a9; }
            QFrame#summary { background: #102b38; border: 1px solid #2a5364; border-radius: 6px; }
            QLabel#summaryCode { color: #ffbd68; font-size: 22px; font-weight: 800; }
            QLabel#summaryText { color: #a9c1cc; font-size: 10px; }
            QLabel#sectionTitle { color: #dfe9ed; font-size: 12px; font-weight: 700; margin-top: 2px; }
            QLabel#steps { color: #91a9b5; font-size: 9px; }
            QCheckBox { color: #91a9b5; font-size: 10px; spacing: 6px; }
            QCheckBox::indicator { width: 14px; height: 14px; }
            QCheckBox::indicator:unchecked { background: #132936; border: 1px solid #3c5d6e; border-radius: 3px; }
            QCheckBox::indicator:checked { background: #36d6c7; border: 1px solid #36d6c7; border-radius: 3px; }
            QPushButton#primaryButton {
                color: #08131b; background: #36d6c7; border: none; border-radius: 5px;
                padding: 6px 10px; font-size: 11px; font-weight: 700;
            }
            QPushButton#primaryButton:hover { background: #67e4d8; }
            QPushButton#secondaryButton {
                color: #b3c6cf; background: #172d3a; border: 1px solid #345367;
                border-radius: 5px; padding: 6px 10px; font-size: 11px; font-weight: 600;
            }
            QLabel#status {
                color: #71909f; background: #091721; border-radius: 4px;
                padding: 3px; font-size: 8px;
            }
        """)
