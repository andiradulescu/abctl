import ctypes
import importlib.machinery
import importlib.util
import pathlib
import struct
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


if __name__ == "__main__":
  unittest.main()
