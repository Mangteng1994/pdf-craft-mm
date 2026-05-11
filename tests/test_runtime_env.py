import unittest
from pathlib import Path

from scripts.runtime_env import ENV_DIR, FLAVORS


class TestRuntimeEnv(unittest.TestCase):
  def test_cpu_flavor_mapping(self):
    cpu = FLAVORS["cpu"]
    self.assertEqual(cpu.package_extra, "cpu")
    self.assertEqual(cpu.device, "cpu")
    self.assertEqual(cpu.env_dir, ENV_DIR / "cpu")

  def test_gpu_flavor_mapping(self):
    gpu = FLAVORS["gpu"]
    self.assertEqual(gpu.package_extra, "cuda")
    self.assertEqual(gpu.device, "cuda")
    self.assertEqual(gpu.env_dir, ENV_DIR / "gpu")

  def test_python_path_is_inside_flavor_directory(self):
    for flavor in FLAVORS.values():
      self.assertIsInstance(flavor.python_path, Path)
      self.assertTrue(str(flavor.python_path).startswith(str(flavor.env_dir)))
