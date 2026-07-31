from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from workers.archive_extract_worker import (
    ArchiveEntryRejected,
    ArchiveExtractWorker,
    validate_archive_entry,
)


class ValidateArchiveEntryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name) / "ffmpeg"
        self.root.mkdir()

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_normal_entry_resolves_inside_target(self) -> None:
        target = validate_archive_entry("ffmpeg-8.0.1/bin/ffmpeg.exe", self.root)
        self.assertTrue(target.is_relative_to(self.root.resolve()))

    def test_parent_traversal_is_rejected(self) -> None:
        with self.assertRaises(ArchiveEntryRejected):
            validate_archive_entry("../../evil.exe", self.root)

    def test_windows_style_traversal_is_rejected(self) -> None:
        with self.assertRaises(ArchiveEntryRejected):
            validate_archive_entry(r"bin\..\..\evil.exe", self.root)

    def test_posix_absolute_path_is_rejected(self) -> None:
        with self.assertRaises(ArchiveEntryRejected):
            validate_archive_entry("/etc/cron.d/evil", self.root)

    def test_windows_drive_path_is_rejected(self) -> None:
        with self.assertRaises(ArchiveEntryRejected):
            validate_archive_entry(r"C:\Windows\System32\evil.dll", self.root)

    def test_unc_path_is_rejected(self) -> None:
        with self.assertRaises(ArchiveEntryRejected):
            validate_archive_entry(r"\\server\share\evil.dll", self.root)

    def test_empty_entry_is_rejected(self) -> None:
        with self.assertRaises(ArchiveEntryRejected):
            validate_archive_entry("   ", self.root)


class _FakeArchive:
    def __init__(self, names: list[str], extracted: list[str]) -> None:
        self._names = names
        self._extracted = extracted

    def getnames(self) -> list[str]:
        return list(self._names)

    def extractall(self, path) -> None:
        self._extracted.append(str(path))

    def __enter__(self) -> "_FakeArchive":
        return self

    def __exit__(self, *_exc) -> None:
        return None


class ArchiveExtractWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.extract_dir = self.root / "ffmpeg"
        self.archive = self.root / "ffmpeg.7z"
        self.archive.write_bytes(b"7z")
        self.extracted: list[str] = []
        self.errors: list[str] = []
        self.successes: list[str] = []
        self._original_py7zr = sys.modules.get("py7zr")

    def tearDown(self) -> None:
        if self._original_py7zr is None:
            sys.modules.pop("py7zr", None)
        else:
            sys.modules["py7zr"] = self._original_py7zr
        self._temp.cleanup()

    def _install_fake_py7zr(self, names: list[str]) -> None:
        sys.modules["py7zr"] = SimpleNamespace(
            SevenZipFile=lambda _path, mode="r": _FakeArchive(names, self.extracted)
        )

    def _run(self, names: list[str], **kwargs) -> None:
        self._install_fake_py7zr(names)
        worker = ArchiveExtractWorker(self.archive, self.extract_dir, **kwargs)
        worker.signals.error.connect(self.errors.append)
        worker.signals.success.connect(self.successes.append)
        worker.run()

    def test_safe_archive_is_extracted(self) -> None:
        self._run(["ffmpeg-8.0.1/bin/ffmpeg.exe"])
        self.assertEqual(self.errors, [])
        self.assertEqual(self.extracted, [str(self.extract_dir)])
        self.assertEqual(self.successes, [str(self.extract_dir)])

    def test_traversal_entry_blocks_extraction(self) -> None:
        self._run(["bin/ffmpeg.exe", "../../evil.exe"])
        self.assertEqual(self.extracted, [])
        self.assertEqual(self.successes, [])
        self.assertTrue(self.errors and "路径穿越" in self.errors[0])

    def test_missing_required_file_is_reported(self) -> None:
        self._run(["ffmpeg-8.0.1/bin/ffmpeg.exe"], required_files=("ffmpeg.exe",))
        self.assertEqual(self.successes, [])
        self.assertTrue(self.errors and "ffmpeg.exe" in self.errors[0])

    def test_required_file_present_after_extraction(self) -> None:
        def fake_extract(path) -> None:
            binary_dir = Path(path) / "bin"
            binary_dir.mkdir(parents=True, exist_ok=True)
            (binary_dir / "ffmpeg.exe").write_bytes(b"exe")

        self._install_fake_py7zr(["ffmpeg-8.0.1/bin/ffmpeg.exe"])
        archive = _FakeArchive(["ffmpeg-8.0.1/bin/ffmpeg.exe"], self.extracted)
        archive.extractall = fake_extract  # type: ignore[method-assign]
        sys.modules["py7zr"] = SimpleNamespace(SevenZipFile=lambda _path, mode="r": archive)

        worker = ArchiveExtractWorker(self.archive, self.extract_dir, required_files=("ffmpeg.exe",))
        worker.signals.error.connect(self.errors.append)
        worker.signals.success.connect(self.successes.append)
        worker.run()

        self.assertEqual(self.errors, [])
        self.assertEqual(self.successes, [str(self.extract_dir)])


if __name__ == "__main__":
    unittest.main()
