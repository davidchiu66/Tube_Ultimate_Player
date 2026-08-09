from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from services import chromium_cookie_extractor as extractor


def _aes_gcm_encrypt(key: bytes, nonce: bytes, plaintext: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    return AESGCM(key).encrypt(nonce, plaintext, None)


class DecryptCookieValueTest(unittest.TestCase):
    def setUp(self) -> None:
        self.key = bytes(range(32))
        self.nonce = bytes(range(12))

    def test_v10_value_round_trips(self) -> None:
        payload = _aes_gcm_encrypt(self.key, self.nonce, b"session=abc123")
        blob = b"v10" + self.nonce + payload
        value = extractor._decrypt_cookie_value(blob, self.key, None)
        self.assertEqual(value, "session=abc123")

    def test_v20_value_strips_32_byte_metadata_prefix(self) -> None:
        plaintext = b"\x00" * 32 + b"secret-token"
        payload = _aes_gcm_encrypt(self.key, self.nonce, plaintext)
        blob = b"v20" + self.nonce + payload
        value = extractor._decrypt_cookie_value(blob, None, self.key)
        self.assertEqual(value, "secret-token")

    def test_missing_key_yields_empty(self) -> None:
        payload = _aes_gcm_encrypt(self.key, self.nonce, b"x=y")
        blob = b"v20" + self.nonce + payload
        self.assertEqual(extractor._decrypt_cookie_value(blob, self.key, None), "")

    def test_wrong_key_fails_tag_and_yields_empty(self) -> None:
        payload = _aes_gcm_encrypt(self.key, self.nonce, b"x=y")
        blob = b"v10" + self.nonce + payload
        self.assertEqual(extractor._decrypt_cookie_value(blob, bytes(32), None), "")

    def test_empty_blob_yields_empty(self) -> None:
        self.assertEqual(extractor._decrypt_cookie_value(b"", self.key, self.key), "")


class NetscapeFileTest(unittest.TestCase):
    def test_writes_tab_separated_rows(self) -> None:
        import tempfile

        rows = [
            extractor.CookieRow(".youtube.com", "SID", "abc", "/", True, 0),
            extractor.CookieRow("www.youtube.com", "PREF", "v=1", "/", False, 0),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "cookies.txt"
            extractor.write_netscape_file(rows, target)
            text = target.read_text(encoding="utf-8")

        lines = [line for line in text.splitlines() if not line.startswith("#")]
        self.assertEqual(len(lines), 2)
        first = lines[0].split("\t")
        self.assertEqual(len(first), 7)
        self.assertEqual(first[0], ".youtube.com")
        self.assertEqual(first[1], "TRUE")  # 以点开头 → 含子域
        self.assertEqual(first[3], "TRUE")  # secure
        self.assertEqual(first[5], "SID")
        self.assertEqual(first[6], "abc")
        second = lines[1].split("\t")
        self.assertEqual(second[1], "FALSE")  # 不以点开头
        self.assertEqual(second[3], "FALSE")

    def test_skips_rows_without_name_or_domain(self) -> None:
        import tempfile

        rows = [
            extractor.CookieRow(".youtube.com", "", "abc", "/", True, 0),
            extractor.CookieRow("", "X", "abc", "/", True, 0),
            extractor.CookieRow(".youtube.com", "GOOD", "v", "/", True, 0),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "cookies.txt"
            extractor.write_netscape_file(rows, target)
            lines = [l for l in target.read_text(encoding="utf-8").splitlines() if not l.startswith("#")]
        self.assertEqual(len(lines), 1)
        self.assertIn("GOOD", lines[0])


class ChromeEpochTest(unittest.TestCase):
    def test_converts_microseconds_since_1601(self) -> None:
        # 2021-01-01T00:00:00Z == 13253932800 秒(1601 起) == 13253932800_000000 微秒
        micros = 13253932800 * 1_000_000
        self.assertEqual(extractor._chrome_epoch_to_unix(micros), 1609459200)

    def test_zero_or_negative_yields_zero(self) -> None:
        self.assertEqual(extractor._chrome_epoch_to_unix(0), 0)
        self.assertEqual(extractor._chrome_epoch_to_unix(-5), 0)


class DomainMatchTest(unittest.TestCase):
    def test_matches_host_and_subdomains(self) -> None:
        self.assertTrue(extractor._domain_matches("www.youtube.com", ".youtube.com"))
        self.assertTrue(extractor._domain_matches("youtube.com", "youtube.com"))
        self.assertFalse(extractor._domain_matches("evil.com", ".youtube.com"))


class GuiSideGuardTest(unittest.TestCase):
    def test_firefox_spec_is_ignored(self) -> None:
        self.assertEqual(extractor.extract_cookies_to_netscape("firefox:default", "https://youtube.com"), "")

    def test_empty_spec_is_ignored(self) -> None:
        self.assertEqual(extractor.extract_cookies_to_netscape("", "https://youtube.com"), "")

    @unittest.skipUnless(sys.platform.startswith("win"), "non-win 分支单独测")
    def test_nonzero_subprocess_returns_empty(self) -> None:
        fake = mock.Mock(returncode=1, stdout="", stderr="boom")
        with mock.patch.object(extractor.subprocess, "run", return_value=fake):
            self.assertEqual(
                extractor.extract_cookies_to_netscape("chrome:Default", "https://youtube.com"),
                "",
            )

    def test_non_windows_returns_empty(self) -> None:
        with mock.patch.object(extractor.sys, "platform", "linux"):
            self.assertEqual(
                extractor.extract_cookies_to_netscape("chrome:Default", "https://youtube.com"),
                "",
            )


class RunCliTest(unittest.TestCase):
    def test_missing_args_returns_usage_code(self) -> None:
        self.assertEqual(extractor.run_cli([]), 2)

    def test_no_cookies_returns_one(self) -> None:
        with mock.patch.object(extractor, "extract_cookie_rows", return_value=[]):
            self.assertEqual(extractor.run_cli(["chrome", "https://x", "out.txt"]), 1)

    def test_extract_exception_is_isolated(self) -> None:
        with mock.patch.object(extractor, "extract_cookie_rows", side_effect=RuntimeError("crash")):
            self.assertEqual(extractor.run_cli(["chrome", "https://x", "out.txt"]), 1)

    def test_success_writes_file_and_returns_zero(self) -> None:
        import tempfile

        rows = [extractor.CookieRow(".youtube.com", "SID", "abc", "/", True, 0)]
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.txt")
            with mock.patch.object(extractor, "extract_cookie_rows", return_value=rows):
                self.assertEqual(extractor.run_cli(["chrome", "https://youtube.com", out]), 0)
            self.assertTrue(os.path.exists(out))


if __name__ == "__main__":
    unittest.main()
