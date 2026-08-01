import ctypes
import importlib.machinery
import importlib.util
import pathlib
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = pathlib.Path(__file__).parents[1] / "abctl"
LOADER = importlib.machinery.SourceFileLoader("abctl", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
abctl = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(abctl)


def make_entry(name, guid_byte, attrs=0):
  entry = bytearray(128)
  entry[0:16] = bytes([guid_byte]) * 16
  struct.pack_into("<Q", entry, 48, attrs)
  encoded_name = name.encode("utf-16-le")
  entry[56:56 + len(encoded_name)] = encoded_name
  return entry


def make_header(current_lba, backup_lba, entries_lba, entry_count=2):
  header = bytearray(abctl.SECTOR_SIZE)
  header[0:8] = b"EFI PART"
  struct.pack_into("<I", header, 12, 92)
  struct.pack_into("<Q", header, 24, current_lba)
  struct.pack_into("<Q", header, 32, backup_lba)
  struct.pack_into("<Q", header, 72, entries_lba)
  struct.pack_into("<I", header, 80, entry_count)
  struct.pack_into("<I", header, 84, 128)
  return header


def make_gpt_image(path):
  primary_entries = make_entry("boot_a", 0x11) + make_entry("boot_b", 0x22)
  backup_entries = make_entry("boot_a", 0x33) + make_entry("boot_b", 0x44)
  primary_header = make_header(1, 19, 2)
  backup_header = make_header(19, 1, 18)

  with open(path, "wb") as image:
    image.truncate(20 * abctl.SECTOR_SIZE)
  with open(path, "r+b") as image:
    abctl._write_gpt(image, 1, primary_header, primary_entries)
    abctl._write_gpt(image, 19, backup_header, backup_entries)


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
         mock.patch.object(abctl, "set_boot_lun") as set_boot_lun, \
         mock.patch("sys.stdout") as stdout:
      self.run_main("--set_active", "1")

    self.assertEqual([call.args[:2] for call in modify.call_args_list], [
      ("/dev/sda", "_b"),
      ("/dev/sde", "_b"),
      ("/dev/sda", "_a"),
      ("/dev/sde", "_a"),
    ])
    self.assertEqual(set_boot_lun.call_args_list, [
      mock.call(abctl.BOOT_LUN_B),
      mock.call(abctl.BOOT_LUN_B),
    ])
    stdout.write.assert_any_call("setting xbl_b lun as boot lun")

  def test_set_active_output_satisfies_openpilot_swap_predicate(self):
    with mock.patch.object(abctl, "get_gpt_active_slot", return_value="_b"), \
         mock.patch.object(abctl, "modify_gpt_attributes"), \
         mock.patch.object(abctl, "set_boot_lun"), \
         mock.patch.object(abctl, "swap_slot_guids"), \
         mock.patch("sys.stdout") as stdout:
      self.run_main("--set_active", "0")

    output = "".join(call.args[0] for call in stdout.write.call_args_list)
    self.assertNotIn("No such file or directory", output)
    self.assertIn("lun as boot lun", output)

  def test_set_active_preflights_boot_lun_before_gpt_writes(self):
    with mock.patch.object(abctl, "get_gpt_active_slot", return_value="_b"), \
         mock.patch.object(abctl, "set_boot_lun", side_effect=RuntimeError("no BSG")), \
         mock.patch.object(abctl, "swap_slot_guids") as swap, \
         mock.patch.object(abctl, "modify_gpt_attributes") as modify:
      with self.assertRaisesRegex(RuntimeError, "no BSG"):
        self.run_main("--set_active", "0")

    swap.assert_not_called()
    modify.assert_not_called()

  def test_mark_successful_clears_unbootable(self):
    unrelated = (1 << 60) | abctl.ATTR_ACTIVE
    attrs = unrelated | abctl.ATTR_UNBOOTABLE
    updated = abctl.mark_successful("boot_b", attrs)

    self.assertEqual(updated & abctl.ATTR_UNBOOTABLE, 0)
    self.assertEqual(updated & abctl.ATTR_SUCCESS, abctl.ATTR_SUCCESS)
    self.assertEqual(updated & unrelated, unrelated)


class GptMirrorTest(unittest.TestCase):
  def read_pair(self, path):
    with open(path, "rb") as image:
      primary, _, backup = abctl._read_gpt_pair(image)
    return primary[1], backup[1]

  def test_attribute_updates_preserve_each_mirrors_unrelated_data(self):
    with tempfile.TemporaryDirectory() as directory:
      path = pathlib.Path(directory) / "disk.img"
      make_gpt_image(path)

      abctl.modify_gpt_attributes(
        path, "_a", lambda name, attrs: attrs | abctl.ATTR_ACTIVE)
      primary, backup = self.read_pair(path)

    self.assertEqual(primary[128:144], bytes([0x22]) * 16)
    self.assertEqual(backup[128:144], bytes([0x44]) * 16)
    self.assertEqual(struct.unpack_from("<Q", primary, 48)[0], abctl.ATTR_ACTIVE)
    self.assertEqual(struct.unpack_from("<Q", backup, 48)[0], abctl.ATTR_ACTIVE)

  def test_guid_swaps_are_applied_to_each_mirror_independently(self):
    with tempfile.TemporaryDirectory() as directory:
      path = pathlib.Path(directory) / "disk.img"
      make_gpt_image(path)

      abctl.swap_slot_guids(path)
      primary, backup = self.read_pair(path)

    self.assertEqual(primary[0:16], bytes([0x22]) * 16)
    self.assertEqual(primary[128:144], bytes([0x11]) * 16)
    self.assertEqual(backup[0:16], bytes([0x44]) * 16)
    self.assertEqual(backup[128:144], bytes([0x33]) * 16)


if __name__ == "__main__":
  unittest.main()
