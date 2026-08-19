from __future__ import annotations

import unittest

from PySide6.QtCore import QPoint, QRect, QSize, Qt

from ui.picture_in_picture import (
    PictureInPictureResizeEdge,
    clamp_geometry_to_screen,
    cursor_shape_for_resize_edge,
    initial_geometry,
    maximum_size_for_screen,
    minimum_size_for_aspect,
    normalized_aspect,
    resize_edge_at,
    resize_geometry,
    snap_geometry_to_edges,
)


class PictureInPictureGeometryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.available = QRect(0, 0, 1920, 1080)

    def test_invalid_aspect_falls_back_to_sixteen_by_nine(self) -> None:
        self.assertAlmostEqual(normalized_aspect(0), 16 / 9)
        self.assertAlmostEqual(normalized_aspect(float("nan")), 16 / 9)

    def test_minimum_size_preserves_common_aspects(self) -> None:
        self.assertEqual(minimum_size_for_aspect(16 / 9), QSize(320, 180))
        self.assertEqual(minimum_size_for_aspect(4 / 3), QSize(320, 240))

    def test_maximum_width_is_half_the_work_area(self) -> None:
        self.assertEqual(maximum_size_for_screen(self.available, 16 / 9), QSize(960, 540))
        four_k = QRect(0, 0, 3840, 2160)
        self.assertEqual(maximum_size_for_screen(four_k, 16 / 9), QSize(1920, 1080))

    def test_first_geometry_is_bottom_right_with_margin(self) -> None:
        geometry = initial_geometry(self.available, 16 / 9)
        self.assertEqual(geometry.size(), QSize(640, 360))
        self.assertEqual(self.available.right() - geometry.right(), 24)
        self.assertEqual(self.available.bottom() - geometry.bottom(), 24)

    def test_saved_geometry_is_clamped_back_onto_screen(self) -> None:
        geometry = initial_geometry(self.available, 16 / 9, QRect(5000, 5000, 640, 360))
        self.assertTrue(self.available.contains(geometry))

    def test_snap_supports_screen_corners(self) -> None:
        geometry = snap_geometry_to_edges(QRect(12, 15, 640, 360), self.available)
        self.assertEqual(geometry.topLeft(), self.available.topLeft())

    def test_resize_keeps_the_opposite_edge_anchored(self) -> None:
        start = QRect(600, 300, 640, 360)
        resized = resize_geometry(
            start,
            PictureInPictureResizeEdge.LEFT,
            QPoint(800, 400),
            16 / 9,
            self.available,
        )
        self.assertEqual(resized.right(), start.right())
        self.assertAlmostEqual(resized.width() / resized.height(), 16 / 9, places=2)

    def test_vertical_resize_uses_the_dragged_height(self) -> None:
        start = QRect(600, 300, 640, 360)
        resized = resize_geometry(
            start,
            PictureInPictureResizeEdge.BOTTOM,
            QPoint(600, 700),
            16 / 9,
            self.available,
        )
        self.assertEqual(resized.top(), start.top())
        self.assertGreater(resized.height(), start.height())
        self.assertAlmostEqual(resized.width() / resized.height(), 16 / 9, places=2)

    def test_corner_resize_can_be_driven_by_vertical_motion(self) -> None:
        start = QRect(600, 300, 640, 360)
        resized = resize_geometry(
            start,
            PictureInPictureResizeEdge.RIGHT | PictureInPictureResizeEdge.BOTTOM,
            QPoint(start.right(), 700),
            16 / 9,
            self.available,
        )
        self.assertEqual(resized.topLeft(), start.topLeft())
        self.assertGreater(resized.height(), start.height())
        self.assertAlmostEqual(resized.width() / resized.height(), 16 / 9, places=2)

    def test_edge_detection_includes_corners(self) -> None:
        edge = resize_edge_at(QRect(100, 100, 640, 360), QPoint(103, 103))
        self.assertEqual(edge, PictureInPictureResizeEdge.LEFT | PictureInPictureResizeEdge.TOP)

    def test_resize_edges_use_standard_directional_cursors(self) -> None:
        cases = {
            PictureInPictureResizeEdge.LEFT: Qt.CursorShape.SizeHorCursor,
            PictureInPictureResizeEdge.BOTTOM: Qt.CursorShape.SizeVerCursor,
            PictureInPictureResizeEdge.LEFT | PictureInPictureResizeEdge.TOP: Qt.CursorShape.SizeFDiagCursor,
            PictureInPictureResizeEdge.RIGHT | PictureInPictureResizeEdge.TOP: Qt.CursorShape.SizeBDiagCursor,
        }
        for edge, expected in cases.items():
            with self.subTest(edge=edge):
                self.assertEqual(cursor_shape_for_resize_edge(edge), expected)

    def test_clamp_never_leaves_the_window_unreachable(self) -> None:
        geometry = clamp_geometry_to_screen(QRect(-500, -500, 640, 360), self.available)
        self.assertEqual(geometry.topLeft(), QPoint(0, 0))


if __name__ == "__main__":
    unittest.main()
