import ctypes
import importlib.machinery
import importlib.util
import pathlib
import struct
import subprocess
import sys
import unittest
from unittest import mock


SCRIPT = pathlib.Path(__file__).parents[1] / "abctl"
LOADER = importlib.machinery.SourceFileLoader("abctl", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
abctl = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(abctl)


class BootLunBsgTest(unittest.TestCase):
  def test_uses_mainline_ufs_bsg_structure_sizes(self):
    captured = {}

    def ioctl(fd, request, arg, mutate):
      self.assertEqual(fd, 23)
      self.assertEqual(request, 0x2285)
      self.assertTrue(mutate)
      self.assertEqual(struct.unpack_from("=I", arg, 4)[0], 0)
      self.assertEqual(struct.unpack_from("=I", arg, 8)[0], 2)

      request_len = struct.unpack_from("=I", arg, 12)[0]
      request_ptr = struct.unpack_from("=Q", arg, 16)[0]
      response_len = struct.unpack_from("=I", arg, 44)[0]
      captured["request"] = ctypes.string_at(request_ptr, request_len)
      captured["request_len"] = request_len
      captured["response_len"] = response_len

    with mock.patch.object(abctl.os, "listdir", return_value=["ufs-bsg0"]), \
         mock.patch.object(abctl.os, "open", return_value=23), \
         mock.patch.object(abctl.os, "close"), \
         mock.patch.object(abctl.fcntl, "ioctl", side_effect=ioctl):
      self.assertTrue(abctl._set_boot_lun_bsg(2))

    self.assertEqual(captured["request_len"], 36)
    self.assertEqual(captured["response_len"], 40)
    self.assertEqual(captured["request"][0:4], struct.pack("<I", 0x16))
    self.assertEqual(captured["request"][4:8], bytes([0x16, 0, 0, 0]))
    self.assertEqual(captured["request"][8:12], bytes([0, 0x81, 0, 0]))
    self.assertEqual(captured["request"][16:20], bytes([0x4, 0, 0, 0]))
    self.assertEqual(captured["request"][24:28], struct.pack(">I", 2))

  def test_returns_false_without_ufs_bsg_device(self):
    with mock.patch.object(abctl.os, "listdir", return_value=["0:0:0:0"]):
      self.assertFalse(abctl._set_boot_lun_bsg(2))


class SlotArgumentTest(unittest.TestCase):
  def test_accepts_only_documented_slot_numbers(self):
    self.assertEqual(abctl.parse_slot_arg("0"), "_a")
    self.assertEqual(abctl.parse_slot_arg("1"), "_b")
    for value in ("2", "a", "b", "", "-1"):
      with self.subTest(value=value), self.assertRaises(ValueError):
        abctl.parse_slot_arg(value)

  def test_invalid_active_slot_exits_before_opening_devices(self):
    result = subprocess.run(
      [sys.executable, SCRIPT, "--set_active", "2"],
      text=True,
      capture_output=True,
      check=False,
    )
    self.assertEqual(result.returncode, 1)
    self.assertEqual(result.stdout, "")
    self.assertIn("expected 0 or 1", result.stderr)
    self.assertNotIn("Traceback", result.stderr)


class MultiDiskAttributeTest(unittest.TestCase):
  def run_main(self, *args):
    with mock.patch.object(abctl.sys, "argv", [str(SCRIPT), *args]):
      abctl.main()

  def test_mark_successful_updates_every_ab_disk(self):
    with mock.patch.object(abctl, "get_current_slot", return_value="_b"), \
         mock.patch.object(abctl, "modify_gpt_attributes") as modify:
      self.run_main("--set_success")

    self.assertEqual([call.args[:2] for call in modify.call_args_list], [
      ("/dev/sda", "_b"),
      ("/dev/sde", "_b"),
    ])

  def test_mark_unbootable_updates_every_ab_disk(self):
    with mock.patch.object(abctl, "modify_gpt_attributes") as modify:
      self.run_main("--set_unbootable", "0")

    self.assertEqual([call.args[:2] for call in modify.call_args_list], [
      ("/dev/sda", "_a"),
      ("/dev/sde", "_a"),
    ])

  def test_set_active_updates_both_slots_on_every_ab_disk(self):
    with mock.patch.object(abctl, "get_gpt_active_slot", return_value="_b"), \
         mock.patch.object(abctl, "modify_gpt_attributes") as modify, \
         mock.patch.object(abctl, "set_boot_lun") as set_boot_lun:
      self.run_main("--set_active", "1")

    self.assertEqual([call.args[:2] for call in modify.call_args_list], [
      ("/dev/sda", "_b"),
      ("/dev/sde", "_b"),
      ("/dev/sda", "_a"),
      ("/dev/sde", "_a"),
    ])
    set_boot_lun.assert_called_once_with(abctl.BOOT_LUN_B)

  def test_mark_successful_clears_unbootable(self):
    unrelated = (1 << 60) | abctl.ATTR_ACTIVE
    attrs = unrelated | abctl.ATTR_UNBOOTABLE
    updated = abctl.mark_successful("boot_b", attrs)

    self.assertEqual(updated & abctl.ATTR_UNBOOTABLE, 0)
    self.assertEqual(updated & abctl.ATTR_SUCCESS, abctl.ATTR_SUCCESS)
    self.assertEqual(updated & unrelated, unrelated)


if __name__ == "__main__":
  unittest.main()
