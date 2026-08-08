"""Full-screen stage-only road-patrol display."""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFontMetrics
from PyQt5.QtWidgets import QLabel, QMainWindow, QSizePolicy

from components.road_patrol_status import RoadPatrolDisplayState


class AutoFitStageLabel(QLabel):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(False)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def setText(self, text):
        super().setText(text)
        self._fit_font()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_font()

    def showEvent(self, event):
        super().showEvent(event)
        self._fit_font()

    def _fit_font(self):
        text = self.text()
        bounds = self.contentsRect()
        available_width = max(1, bounds.width() - 48)
        available_height = max(1, bounds.height() - 48)
        low = 24
        high = 320
        best = low
        while low <= high:
            size = (low + high) // 2
            font = self.font()
            font.setPointSize(size)
            font.setBold(True)
            metrics = QFontMetrics(font)
            if (
                metrics.horizontalAdvance(text) <= available_width
                and metrics.height() <= available_height
            ):
                best = size
                low = size + 1
            else:
                high = size - 1
        font = self.font()
        font.setPointSize(best)
        font.setBold(True)
        self.setFont(font)


class RoadPatrolWindow(QMainWindow):
    """Display only the current task stage; no position or path is rendered."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("巡路任务")
        self.setMinimumSize(640, 360)
        self._display_state = RoadPatrolDisplayState()
        self.stage_label = AutoFitStageLabel(self._display_state.label)
        self.stage_label.setObjectName("roadPatrolStage")
        self.setCentralWidget(self.stage_label)
        self.setStyleSheet(
            """
            QMainWindow { background: #101214; }
            QWidget { font-family: 'Noto Sans CJK SC', 'Microsoft YaHei',
                       'WenQuanYi Micro Hei', sans-serif; }
            QLabel#roadPatrolStage {
                background: #101214;
                color: #f5f7f8;
                padding: 24px;
            }
            """
        )

    def update_snapshot(self, snapshot):
        self._display_state.update_snapshot(snapshot)
        label = self._display_state.label
        if self.stage_label.text() != label:
            self.stage_label.setText(label)


__all__ = ["RoadPatrolWindow"]
