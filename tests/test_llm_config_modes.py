import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import llm_config
import web_app
from pdf_craft.llm.codex_cli import CodexCLIExecutor


class TestLLMConfigModes(unittest.TestCase):
  def test_mode_switch_does_not_pollute_other_values(self):
    with tempfile.TemporaryDirectory() as temp_dir:
      temp_env = Path(temp_dir) / ".env"
      original_web_env = web_app.ENV_PATH
      original_llm_env = llm_config.ENV_PATH
      web_app.ENV_PATH = temp_env
      llm_config.ENV_PATH = temp_env
      try:
        web_app._write_env_values({  # pylint: disable=protected-access
          "PDF_CRAFT_LLM_MODE": "api_key",
          "PDF_CRAFT_LLM_API_KEY": "sk-test-123456",
          "PDF_CRAFT_LLM_BASE_URL": "https://example.com/v1",
          "PDF_CRAFT_LLM_MODEL": "example-model",
        })
        web_app._write_env_values({  # pylint: disable=protected-access
          "PDF_CRAFT_LLM_MODE": "codex_cli",
          "PDF_CRAFT_CODEX_CLI_PATH": "C:/Tools/codex.cmd",
        })

        values = web_app._read_env_values()  # pylint: disable=protected-access
        self.assertEqual(values["PDF_CRAFT_LLM_MODE"], "codex_cli")
        self.assertEqual(values["PDF_CRAFT_CODEX_CLI_PATH"], "C:/Tools/codex.cmd")
        self.assertEqual(values["PDF_CRAFT_LLM_API_KEY"], "sk-test-123456")
        self.assertEqual(values["PDF_CRAFT_LLM_BASE_URL"], "https://example.com/v1")
        self.assertEqual(values["PDF_CRAFT_LLM_MODEL"], "example-model")
      finally:
        web_app.ENV_PATH = original_web_env
        llm_config.ENV_PATH = original_llm_env

  def test_detect_codex_cli_endpoint(self):
    client = TestClient(web_app.app)
    with patch.object(web_app, "find_codex_cli_path", return_value=Path("C:/Tools/codex.cmd")):
      response = client.post("/api/config/detect-codex-cli")
    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.json()["path"], "C:\\Tools\\codex.cmd")

  def test_detect_codex_cli_endpoint_not_found(self):
    client = TestClient(web_app.app)
    with patch.object(web_app, "find_codex_cli_path", return_value=None):
      response = client.post("/api/config/detect-codex-cli")
    self.assertEqual(response.status_code, 404)
    self.assertIn("未检测到可用的 Codex CLI", response.json()["detail"])

  def test_create_llm_uses_codex_cli_mode(self):
    original_env = dict(os.environ)
    try:
      os.environ["PDF_CRAFT_LLM_MODE"] = "codex_cli"
      os.environ["PDF_CRAFT_CODEX_CLI_PATH"] = "C:/Tools/codex.cmd"
      os.environ["PDF_CRAFT_TOKEN_ENCODING"] = "o200k_base"
      os.environ["PDF_CRAFT_LLM_RETRY_TIMES"] = "1"
      os.environ["PDF_CRAFT_LLM_RETRY_INTERVAL_SECONDS"] = "0"
      with patch.object(llm_config, "_load_env_file"), \
           patch.object(llm_config, "validate_codex_cli_path", return_value=Path("C:/Tools/codex.cmd")):
        llm = llm_config.create_llm()
      self.assertIsInstance(llm._executor, CodexCLIExecutor)  # pylint: disable=protected-access
    finally:
      os.environ.clear()
      os.environ.update(original_env)

  def test_find_codex_cli_path_falls_back_to_windows_locations(self):
    with patch.object(llm_config.shutil, "which", return_value=None), \
         patch.dict(os.environ, {
           "APPDATA": "C:/Users/test/AppData/Roaming",
           "LOCALAPPDATA": "C:/Users/test/AppData/Local",
           "ProgramFiles": "C:/Program Files",
         }, clear=False), \
         patch.object(llm_config, "validate_codex_cli_path") as validate_mock:
      validate_mock.side_effect = lambda value: Path(value)
      found = llm_config.find_codex_cli_path()

    self.assertIsNotNone(found)
    self.assertEqual(found, Path("C:/Users/test/AppData/Roaming/npm/codex"))

  def test_codex_executor_closes_temp_file_handle(self):
    executor = CodexCLIExecutor(
      cli_path="C:/Tools/codex.cmd",
      timeout=1.0,
      retry_times=0,
      retry_interval_seconds=0.0,
      create_logger=lambda: None,
    )

    with patch("pdf_craft.llm.codex_cli.tempfile.mkstemp", return_value=(123, "C:/Temp/out.txt")), \
         patch("pdf_craft.llm.codex_cli.os.close") as close_mock:
      path = executor._create_temp_output_path()  # pylint: disable=protected-access

    self.assertEqual(path, Path("C:/Temp/out.txt"))
    close_mock.assert_called_once_with(123)

  def test_codex_executor_xml_prompt_is_hard_constrained(self):
    executor = CodexCLIExecutor(
      cli_path="C:/Tools/codex.cmd",
      timeout=1.0,
      retry_times=0,
      retry_interval_seconds=0.0,
      create_logger=lambda: None,
    )

    wrapped = executor._wrap_prompt("original prompt", "_encode_xml")  # pylint: disable=protected-access

    self.assertIn("valid XML only", wrapped)
    self.assertIn("rooted at <response>", wrapped)
    self.assertIn("original prompt", wrapped)
