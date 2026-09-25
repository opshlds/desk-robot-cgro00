"""Several boards, each with its own roles (brain/devices.py)."""

import unittest

from brain.devices import ROLES, Device, Registry, RoleTaken, parse_roles


class ParseRolesTests(unittest.TestCase):
    def test_classic_robot_gets_every_role(self):
        self.assertEqual(parse_roles({"type": "hello"}), frozenset(ROLES))

    def test_declared_roles(self):
        self.assertEqual(parse_roles({"roles": ["mic", "speaker"]}), {"mic", "speaker"})

    def test_unknown_roles_are_ignored(self):
        self.assertEqual(parse_roles({"roles": ["camera", "laser"]}), {"camera"})

    def test_nothing_usable_is_refused(self):
        for bad in ([], ["laser"], "mic", 3):
            with self.assertRaises(ValueError):
                parse_roles({"roles": bad})


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.reg = Registry()
        self.voice = Device("yahboom", frozenset({"mic", "speaker"}), who="xiaozhi-bridge")
        self.cam = Device("xiao", frozenset({"camera", "neck"}), who="xiao")
        self.face = Device("amoled", frozenset({"face"}), who="amoled")
        for d in (self.voice, self.cam, self.face):
            self.reg.add(d)

    def test_routing_by_message_type(self):
        self.assertEqual(self.reg.targets("speak_begin"), ["yahboom"])
        self.assertEqual(self.reg.targets("volume"), ["yahboom"])
        self.assertEqual(self.reg.targets("mic"), ["yahboom"])
        self.assertEqual(self.reg.targets("pan"), ["xiao"])
        self.assertEqual(self.reg.targets("stream"), ["xiao"])
        self.assertEqual(sorted(self.reg.targets("emotion")), ["amoled", "xiao", "yahboom"])
        self.assertEqual(sorted(self.reg.targets("asleep")), ["amoled", "xiao", "yahboom"])

    def test_owner_lookup(self):
        self.assertEqual(self.reg.conn_for("speaker"), "yahboom")
        self.assertEqual(self.reg.conn_for("face"), "amoled")
        self.assertTrue(self.reg.has_role("xiao", "camera"))
        self.assertFalse(self.reg.has_role("xiao", "mic"))

    def test_a_role_has_one_owner(self):
        with self.assertRaises(RoleTaken):
            self.reg.add(Device("second-speaker", frozenset({"speaker"})))
        self.assertEqual(self.reg.conn_for("speaker"), "yahboom")

    def test_role_frees_up_on_disconnect(self):
        self.reg.remove("yahboom")
        self.assertIsNone(self.reg.conn_for("mic"))
        self.assertEqual(self.reg.targets("speak_begin"), [])
        self.reg.add(Device("yahboom-again", frozenset({"mic", "speaker"})))
        self.assertEqual(self.reg.conn_for("speaker"), "yahboom-again")

    def test_classic_robot_takes_everything(self):
        reg = Registry()
        reg.add(Device("desk-robot", frozenset(ROLES)))
        for t in ("speak_begin", "pan", "stream", "mic", "emotion"):
            self.assertEqual(reg.targets(t), ["desk-robot"])
        with self.assertRaises(RoleTaken):
            reg.add(Device("xiao", frozenset({"camera"})))


if __name__ == "__main__":
    unittest.main()
