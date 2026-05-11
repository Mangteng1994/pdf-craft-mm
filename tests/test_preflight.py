import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import web_app


class TestPreflight(unittest.TestCase):
  def setUp(self):
    self._original_env = dict(os.environ)
    self._temp_dir = tempfile.TemporaryDirectory()
    self.root = Path(self._temp_dir.name)
    self.env_path = self.root / ".env"
    self.input_dir = self.root / "inputs"
    self.work_dir = self.root / "work"
    self.dist_dir = self.root / "dist"
    self.model_dir = self.root / "models"
    self.input_dir.mkdir()
    self.work_dir.mkdir()
    self.dist_dir.mkdir()
    self.model_dir.mkdir()
    self.original_web_env = web_app.ENV_PATH
    web_app.ENV_PATH = self.env_path

  def tearDown(self):
    web_app.ENV_PATH = self.original_web_env
    os.environ.clear()
    os.environ.update(self._original_env)
    self._temp_dir.cleanup()

  def _write_config(self, **overrides):
    values = {
      "PDF_CRAFT_LLM_MODE": "api_key",
      "PDF_CRAFT_LLM_API_KEY": "sk-test-123456",
      "PDF_CRAFT_LLM_BASE_URL": "https://example.com",
      "PDF_CRAFT_INPUT_DIR": str(self.input_dir),
      "PDF_CRAFT_WORK_DIR": str(self.work_dir),
      "PDF_CRAFT_DIST_DIR": str(self.dist_dir),
      "PDF_CRAFT_MODEL_DIR": str(self.model_dir),
      "PDF_CRAFT_DEVICE": "cpu",
    }
    values.update(overrides)
    web_app._write_env_values(values)  # pylint: disable=protected-access

  def test_preflight_blocks_missing_api_key(self):
    self._write_config(PDF_CRAFT_LLM_API_KEY="")

    with patch.object(
      web_app,
      "_check_http_reachable",
      return_value=web_app._preflight_item("llm_url", "LLM 服务地址", "ok", "服务地址可连接"),  # pylint: disable=protected-access
    ):
      result = web_app._run_preflight()  # pylint: disable=protected-access

    self.assertFalse(result["can_start"])
    self.assertTrue(any(item["id"] == "llm_key" and item["blocking"] for item in result["items"]))

  def test_preflight_warns_when_output_file_exists(self):
    self._write_config()
    pdf = self.input_dir / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    (self.dist_dir / "book.epub").write_bytes(b"epub")

    with patch.object(
      web_app,
      "_check_http_reachable",
      return_value=web_app._preflight_item("llm_url", "LLM 服务地址", "ok", "服务地址可连接"),  # pylint: disable=protected-access
    ):
      result = web_app._run_preflight("book.pdf", None)  # pylint: disable=protected-access

    self.assertTrue(result["can_start"])
    self.assertTrue(any(item["id"] == "output_file" and item["status"] == "warn" for item in result["items"]))

  def test_preflight_returns_runtime_guides(self):
    self._write_config(PDF_CRAFT_DEVICE="cuda", PDF_CRAFT_EXTRACT_FORMULA="true")

    runtime = {
      "python": str(self.root / ".venvs" / "cpu" / "Scripts" / "python.exe"),
      "onnxruntime": "1.21.0",
      "onnxruntime_gpu": None,
      "providers": ["CPUExecutionProvider"],
    }

    with patch.object(
      web_app,
      "_check_http_reachable",
      return_value=web_app._preflight_item("llm_url", "LLM 服务地址", "ok", "服务地址可连接"),  # pylint: disable=protected-access
    ), patch.object(web_app, "_runtime_info", return_value=runtime), patch.object(
      web_app.shutil,
      "which",
      side_effect=lambda name: None if name == "latex" else "C:/Windows/System32/winget.exe",
    ):
      result = web_app._run_preflight()  # pylint: disable=protected-access

    guides = {item["id"]: item for item in result["guides"]}
    self.assertEqual({"cpu", "cuda", "latex"}, set(guides))
    self.assertEqual("attention", guides["cuda"]["status"])
    self.assertTrue(any("run-web gpu" in command["command"] for command in guides["cuda"]["commands"]))
    self.assertEqual("attention", guides["latex"]["status"])
    self.assertTrue(any("MiKTeX" in command["command"] for command in guides["latex"]["commands"]))

  def test_start_job_rejects_failed_preflight(self):
    self._write_config(PDF_CRAFT_LLM_API_KEY="")
    pdf = self.input_dir / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    client = TestClient(web_app.app)

    response = client.post("/api/jobs", json={"pdf_name": "book.pdf"})

    self.assertEqual(response.status_code, 409)
    self.assertEqual(response.json()["detail"]["message"], "预检未通过，任务未加入队列")

  def test_preflight_validates_codex_reasoning_effort(self):
    self._write_config(
      PDF_CRAFT_LLM_MODE="codex_cli",
      PDF_CRAFT_CODEX_CLI_PATH="C:/Tools/codex.cmd",
      PDF_CRAFT_CODEX_MODEL_REASONING_EFFORT="extreme",
    )

    with patch.object(web_app, "validate_codex_cli_path", return_value=Path("C:/Tools/codex.cmd")):
      result = web_app._run_preflight()  # pylint: disable=protected-access

    self.assertFalse(result["can_start"])
    self.assertTrue(any(item["id"] == "codex_reasoning" and item["blocking"] for item in result["items"]))
