from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from services.update_service import (
    ReleaseAsset,
    ReleaseInfo,
    UpdateService,
    ensure_trusted_download_url,
    extract_sha256_for,
    normalize_sha256,
    sha256_file,
    verify_downloaded_file,
)
from workers.update_download_worker import UpdateDownloadWorker


PAYLOAD = b"tube-ultimate-player-setup"
PAYLOAD_SHA256 = "b0eb2b9d34a41b1a3ba79ff0f4c1e8f2ff9d0a1dd0e5c8dbb5f8bbaee9c0b2d0"


def _release(assets: list[ReleaseAsset], body: str = "") -> ReleaseInfo:
    return ReleaseInfo(
        tag_name="v9.9.9",
        name="9.9.9",
        published_at="",
        body=body,
        html_url="https://github.com/davidchiu66/Tube_Ultimate_Player/releases",
        prerelease=False,
        assets=assets,
    )


class TrustedUrlTests(unittest.TestCase):
    def test_https_github_url_is_accepted(self) -> None:
        url = "https://objects.githubusercontent.com/x/setup.exe"
        self.assertEqual(ensure_trusted_download_url(url), url)

    def test_plain_http_is_rejected(self) -> None:
        with self.assertRaises(RuntimeError):
            ensure_trusted_download_url("http://github.com/x/setup.exe")

    def test_foreign_host_is_rejected(self) -> None:
        with self.assertRaises(RuntimeError):
            ensure_trusted_download_url("https://evil.example/setup.exe")

    def test_lookalike_host_is_rejected(self) -> None:
        with self.assertRaises(RuntimeError):
            ensure_trusted_download_url("https://github.com.evil.example/setup.exe")

    def test_custom_allowlist_is_honoured(self) -> None:
        url = "https://www.gyan.dev/ffmpeg/builds/packages/x.7z"
        self.assertEqual(ensure_trusted_download_url(url, ("gyan.dev",)), url)


class HashParsingTests(unittest.TestCase):
    def test_normalize_accepts_prefixed_digest(self) -> None:
        self.assertEqual(normalize_sha256(f"sha256:{PAYLOAD_SHA256.upper()}"), PAYLOAD_SHA256)

    def test_normalize_rejects_other_algorithms(self) -> None:
        self.assertEqual(normalize_sha256("md5:" + "a" * 32), "")

    def test_extract_from_checksum_manifest(self) -> None:
        text = f"{'a' * 64}  other.zip\n{PAYLOAD_SHA256}  setup.exe\n"
        self.assertEqual(extract_sha256_for(text, "setup.exe"), PAYLOAD_SHA256)

    def test_extract_from_bare_sidecar_only_when_allowed(self) -> None:
        self.assertEqual(extract_sha256_for(PAYLOAD_SHA256, "setup.exe"), "")
        self.assertEqual(extract_sha256_for(PAYLOAD_SHA256, "setup.exe", allow_bare=True), PAYLOAD_SHA256)


class VerifyDownloadedFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.path = Path(self._temp.name) / "setup.exe"
        self.path.write_bytes(PAYLOAD)
        self.digest = sha256_file(self.path)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_matching_size_and_hash_pass(self) -> None:
        verify_downloaded_file(self.path, expected_size=len(PAYLOAD), expected_sha256=self.digest)

    def test_size_mismatch_is_rejected(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            verify_downloaded_file(self.path, expected_size=len(PAYLOAD) + 1)
        self.assertIn("大小不符", str(ctx.exception))

    def test_hash_mismatch_is_rejected(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            verify_downloaded_file(self.path, expected_sha256="f" * 64)
        self.assertIn("SHA256", str(ctx.exception))

    def test_empty_file_is_rejected(self) -> None:
        self.path.write_bytes(b"")
        with self.assertRaises(RuntimeError):
            verify_downloaded_file(self.path)


class ResolveExpectedHashTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = UpdateService(SimpleNamespace(effective_proxy=lambda: ("", "")))

    def test_asset_digest_is_preferred(self) -> None:
        asset = ReleaseAsset("setup.exe", "https://github.com/a/setup.exe", 10, digest=f"sha256:{PAYLOAD_SHA256}")
        self.assertEqual(self.service.resolve_expected_sha256(_release([asset]), asset), PAYLOAD_SHA256)

    def test_checksum_asset_is_used_when_digest_missing(self) -> None:
        asset = ReleaseAsset("setup.exe", "https://github.com/a/setup.exe", 10)
        sums = ReleaseAsset("SHA256SUMS.txt", "https://github.com/a/SHA256SUMS.txt", 100)
        manifest = f"{PAYLOAD_SHA256}  setup.exe\n".encode()
        with patch.object(self.service, "open_url", return_value=_response(manifest)):
            digest = self.service.resolve_expected_sha256(_release([asset, sums]), asset)
        self.assertEqual(digest, PAYLOAD_SHA256)

    def test_release_body_is_last_resort(self) -> None:
        asset = ReleaseAsset("setup.exe", "https://github.com/a/setup.exe", 10)
        body = f"### 校验值\n\n| setup.exe | `{PAYLOAD_SHA256}` |\n"
        self.assertEqual(self.service.resolve_expected_sha256(_release([asset], body), asset), PAYLOAD_SHA256)

    def test_missing_hash_returns_empty(self) -> None:
        asset = ReleaseAsset("setup.exe", "https://github.com/a/setup.exe", 10)
        self.assertEqual(self.service.resolve_expected_sha256(_release([asset]), asset), "")


class _Response(io.BytesIO):
    def __init__(self, data: bytes, url: str = "https://objects.githubusercontent.com/a/setup.exe") -> None:
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}
        self._url = url

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def _response(data: bytes, url: str = "https://objects.githubusercontent.com/a/setup.exe") -> _Response:
    return _Response(data, url)


class DownloadWorkerVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.target = Path(self._temp.name) / "setup.exe"
        self.service = SimpleNamespace(open_url=lambda _url: _response(PAYLOAD))
        self.errors: list[str] = []
        self.successes: list[str] = []

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _run(self, **kwargs) -> UpdateDownloadWorker:
        worker = UpdateDownloadWorker(
            self.service,
            "https://github.com/davidchiu66/Tube_Ultimate_Player/releases/download/v9/setup.exe",
            self.target,
            "setup.exe",
            **kwargs,
        )
        worker.signals.error.connect(self.errors.append)
        worker.signals.success.connect(self.successes.append)
        worker.run()
        return worker

    def test_valid_download_is_kept(self) -> None:
        self._run(expected_size=len(PAYLOAD), expected_sha256=sha256_file_bytes(PAYLOAD))
        self.assertEqual(self.errors, [])
        self.assertEqual(self.successes, [str(self.target)])
        self.assertEqual(self.target.read_bytes(), PAYLOAD)

    def test_tampered_download_is_discarded(self) -> None:
        self._run(expected_sha256="f" * 64)
        self.assertFalse(self.target.exists())
        self.assertFalse(self.target.with_suffix(".exe.part").exists())
        self.assertTrue(self.errors and "SHA256" in self.errors[0])

    def test_untrusted_url_is_refused_before_download(self) -> None:
        worker = UpdateDownloadWorker(self.service, "http://evil.example/setup.exe", self.target, "setup.exe")
        worker.signals.error.connect(self.errors.append)
        worker.run()
        self.assertFalse(self.target.exists())
        self.assertTrue(self.errors)

    def test_redirect_to_untrusted_host_is_refused(self) -> None:
        self.service.open_url = lambda _url: _response(PAYLOAD, "https://evil.example/setup.exe")
        self._run()
        self.assertFalse(self.target.exists())
        self.assertTrue(self.errors)

    def test_resolver_result_is_used_for_verification(self) -> None:
        self._run(expected_sha256_resolver=lambda: "f" * 64)
        self.assertFalse(self.target.exists())
        self.assertTrue(self.errors)

    def test_size_mismatch_is_rejected(self) -> None:
        self._run(expected_size=len(PAYLOAD) + 5)
        self.assertFalse(self.target.exists())
        self.assertTrue(self.errors and "大小不符" in self.errors[0])


def sha256_file_bytes(data: bytes) -> str:
    from hashlib import sha256

    return sha256(data).hexdigest()


if __name__ == "__main__":
    unittest.main()
