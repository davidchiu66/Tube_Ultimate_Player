from __future__ import annotations

import unittest
from pathlib import Path

from PySide6.QtCore import QByteArray

from ui.thumbnail_cache import _looks_like_complete_image


class ThumbnailLoadingTests(unittest.TestCase):
    def test_complete_png_is_accepted(self) -> None:
        data = QByteArray(Path("docs/assets/icons/app-icon-16.png").read_bytes())

        self.assertTrue(_looks_like_complete_image(data))

    def test_truncated_png_is_rejected_before_qt_decode(self) -> None:
        data = QByteArray(Path("docs/assets/icons/app-icon-16.png").read_bytes()[:64])

        self.assertFalse(_looks_like_complete_image(data))

    def test_html_error_payload_is_rejected(self) -> None:
        data = QByteArray(b"<html><body>403 Forbidden</body></html>")

        self.assertFalse(_looks_like_complete_image(data))


if __name__ == "__main__":
    unittest.main()
