"""The live-view page (brain/eyes.py): where it listens, and its password."""

import base64
import json
import threading
import time
import unittest
import urllib.error
import urllib.request

from brain import eyes as eyesmod
from brain.eyes import Eyes, check_basic_auth, host_allowed


def basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


class Pieces(unittest.TestCase):
    def test_basic_auth(self):
        self.assertTrue(check_basic_auth(None, ""))                       # no password set: open
        self.assertTrue(check_basic_auth(basic("any", "s3cret"), "s3cret"))
        self.assertTrue(check_basic_auth(basic("", "s3cret"), "s3cret"))   # user name doesn't matter
        self.assertTrue(check_basic_auth(basic("me", "a:b"), "a:b"))       # colons in the password
        self.assertFalse(check_basic_auth(None, "s3cret"))
        self.assertFalse(check_basic_auth(basic("me", "wrong"), "s3cret"))
        self.assertFalse(check_basic_auth("Basic !!!notbase64", "s3cret"))
        self.assertFalse(check_basic_auth("Bearer s3cret", "s3cret"))
        self.assertFalse(check_basic_auth("Basic " + base64.b64encode(b"nocolon").decode(), "nocolon"))

    def test_host_allowed(self):
        self.assertTrue(host_allowed("localhost:8766", "127.0.0.1", ""))
        self.assertTrue(host_allowed("[::1]:8766", "127.0.0.1", ""))
        self.assertFalse(host_allowed("evil.example:8766", "127.0.0.1", ""))       # DNS rebinding
        self.assertTrue(host_allowed("192.168.1.99:8766", "192.168.1.99", ""))
        self.assertFalse(host_allowed("ai1:8766", "192.168.1.99", ""))
        self.assertTrue(host_allowed("ai1:8766", "0.0.0.0", "pw"))                 # any name, with a password
        self.assertFalse(host_allowed("ai1:8766", "0.0.0.0", ""))
        self.assertFalse(host_allowed(None, "127.0.0.1", ""))

    def test_every_address_needs_a_password(self):
        with self.assertRaises(ValueError):
            Eyes().serve(18769, "0.0.0.0", "")


class Served(unittest.TestCase):
    """A real server on 127.0.0.1 with a password."""

    PORT = 18767

    @classmethod
    def setUpClass(cls):
        cls.eyes = Eyes()
        cls.calls = []
        cls.eyes.command_handler = lambda action, payload: cls.calls.append((action, payload)) or {"did": action}
        cls.eyes.serve(cls.PORT, "127.0.0.1", "n0t-the-real-one")
        open_ = Eyes()
        open_.serve(cls.PORT + 1, "127.0.0.1", "")
        time.sleep(0.2)

    def get(self, path, auth=None, host=None, port=None):
        req = urllib.request.Request(f"http://127.0.0.1:{port or self.PORT}{path}")
        if auth:
            req.add_header("Authorization", auth)
        if host:
            req.add_header("Host", host)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def post(self, path, body, auth=None, origin=None, console=True):
        req = urllib.request.Request(f"http://127.0.0.1:{self.PORT}{path}", data=json.dumps(body).encode(),
                                     method="POST", headers={"Content-Type": "application/json"})
        if auth:
            req.add_header("Authorization", auth)
        if console:
            req.add_header("X-Rocky-Console", "1")
        if origin:
            req.add_header("Origin", origin)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, None

    def test_asks_for_the_password(self):
        code, headers, _ = self.get("/")
        self.assertEqual(code, 401)
        self.assertIn("Basic", headers.get("WWW-Authenticate", ""))

    def test_wrong_password_is_refused_slowly(self):
        t = time.monotonic()
        code, _, _ = self.get("/status", auth=basic("me", "guess"))
        self.assertEqual(code, 401)
        self.assertGreater(time.monotonic() - t, 0.9)

    def test_right_password_gets_everything(self):
        ok = basic("anyone", "n0t-the-real-one")
        code, _, body = self.get("/", auth=ok)
        self.assertEqual(code, 200)
        self.assertIn(b"<", body)
        code, _, body = self.get("/status", auth=ok)
        self.assertEqual(code, 200)
        self.assertIn("fps", json.loads(body))
        self.assertEqual(self.get("/frame", auth=ok)[0], 503)             # no camera frame yet

    def test_controls_need_password_header_and_same_origin(self):
        ok = basic("me", "n0t-the-real-one")
        self.assertEqual(self.post("/api/emotion", {"name": "happy"})[0], 401)
        self.assertEqual(self.post("/api/emotion", {"name": "happy"}, auth=ok, console=False)[0], 403)
        self.assertEqual(self.post("/api/emotion", {"name": "happy"}, auth=ok, origin="http://evil.example")[0], 403)
        code, body = self.post("/api/emotion", {"name": "happy"}, auth=ok, origin=f"http://127.0.0.1:{self.PORT}")
        self.assertEqual((code, body), (200, {"ok": True, "did": "emotion"}))
        self.assertIn(("emotion", {"name": "happy"}), self.calls)

    def test_rebinding_host_is_refused_even_with_the_password(self):
        code, _, _ = self.get("/", auth=basic("me", "n0t-the-real-one"), host=f"evil.example:{self.PORT}")
        self.assertEqual(code, 403)

    def test_no_password_set_means_open(self):
        self.assertEqual(self.get("/", port=self.PORT + 1)[0], 200)


if __name__ == "__main__":
    unittest.main()
