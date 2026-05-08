"""Fullscreen background pixmap with *cover* semantics (crop center — no distortion)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPaintEvent, QPainter, QPixmap
from PySide6.QtWidgets import QWidget


class CoverImageWidget(QWidget):
    """Fills geometry with ``image_path`` centered; aspect preserved; excess cropped."""

    def __init__(self, image_path: str | Path | None = None, *, fallback_hex: str = "#010c25") -> None:
        super().__init__()
        self._src: Path | None = Path(image_path) if image_path else None
        self._fallback = QColor(fallback_hex)
        self._pix: QPixmap | None = None
        self.setAttribute(Qt.WA_StyledBackground, False)
        if self._src and self._src.is_file():
            self._pix = QPixmap(str(self._src))
        self.setMinimumSize(1, 1)

    def set_image_path(self, path: str | Path | None) -> None:
        self._src = Path(path) if path else None
        self._pix = QPixmap(str(self._src)) if self._src and self._src.is_file() else None
        self.update()

    def resizeEvent(self, _event) -> None:  # noqa: N802
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        rect = self.rect()
        if not rect.width() or not rect.height():
            return
        if self._pix is None or self._pix.isNull():
            painter.fillRect(rect, self._fallback)
            return
        tw, th = rect.width(), rect.height()
        scaled = self._pix.scaled(
            tw,
            th,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        sx = max(0, (scaled.width() - tw) // 2)
        sy = max(0, (scaled.height() - th) // 2)
        cropped = scaled.copy(QRect(sx, sy, tw, th))
        painter.drawPixmap(rect.x(), rect.y(), cropped)
