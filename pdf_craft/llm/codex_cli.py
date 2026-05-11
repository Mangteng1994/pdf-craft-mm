from __future__ import annotations

import os
import subprocess
import tempfile
from io import StringIO
from logging import Logger
from pathlib import Path
from time import sleep
from typing import Any, Callable

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from llm_config import ROOT_DIR, _codex_command_prefix


class CodexCLIExecutor:
  def __init__(
      self,
      cli_path: str,
      model: str | None,
      reasoning_effort: str | None,
      timeout: float | None,
      retry_times: int,
      retry_interval_seconds: float,
      create_logger: Callable[[], Logger | None],
    ) -> None:
    self._cli_path = Path(cli_path)
    self._model = model.strip() if model else None
    self._reasoning_effort = reasoning_effort.strip().lower() if reasoning_effort else None
    self._timeout = timeout
    self._retry_times = retry_times
    self._retry_interval_seconds = retry_interval_seconds
    self._create_logger = create_logger

  def request(self, input: LanguageModelInput, parser: Callable[[str], Any]) -> Any:
    result: Any | None = None
    last_error: Exception | None = None
    did_success = False
    logger = self._create_logger()
    prompt = self._input2str(input)

    if logger is not None:
      logger.debug(f"[[Request]]:\n{prompt}\n")

    for i in range(self._retry_times + 1):
      try:
        response = self._invoke_cli(
          prompt=prompt,
          parser_name=getattr(parser, "__name__", ""),
        )
        if logger is not None:
          logger.debug(f"[[Response]]:\n{response}\n")
      except Exception as err:  # pylint: disable=broad-except
        last_error = err
        if logger is not None:
          logger.warning(f"codex cli request failed, retrying... ({i + 1} times)")
        if self._retry_interval_seconds > 0.0 and i < self._retry_times:
          sleep(self._retry_interval_seconds)
        continue

      try:
        result = parser(response)
        did_success = True
        break
      except Exception as err:  # pylint: disable=broad-except
        last_error = err
        if logger is not None:
          logger.warning(f"codex cli response parsing failed, retrying... ({i + 1} times)")
        if self._retry_interval_seconds > 0.0 and i < self._retry_times:
          sleep(self._retry_interval_seconds)

    if not did_success:
      if last_error is None:
        raise RuntimeError("Codex CLI request failed with unknown error")
      raise last_error

    return result

  def _invoke_cli(self, prompt: str, parser_name: str = "") -> str:
    output_path = self._create_temp_output_path()
    try:
      command = _codex_command_prefix(self._cli_path) + [
        "exec",
        "--skip-git-repo-check",
        "--color",
        "never",
        "--sandbox",
        "read-only",
        "-C",
        str(ROOT_DIR),
        "--output-last-message",
        str(output_path),
      ]
      if self._model:
        command.extend(["--model", self._model])
      if self._reasoning_effort:
        command.extend(["--config", f'model_reasoning_effort="{self._reasoning_effort}"'])
      command.append("-")
      completed = subprocess.run(
        command,
        input=self._wrap_prompt(prompt, parser_name),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=self._timeout,
        check=False,
      )
      if completed.returncode != 0:
        details = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(details or f"Codex CLI exited with code {completed.returncode}")

      response = ""
      if output_path.exists():
        response = output_path.read_text(encoding="utf-8").strip()
      if response:
        return response

      fallback = (completed.stdout or completed.stderr).strip()
      if fallback:
        return fallback
      raise RuntimeError("Codex CLI returned an empty response")
    finally:
      try:
        output_path.unlink(missing_ok=True)
      except PermissionError:
        # On Windows the CLI may still be releasing the handle.
        pass

  def _create_temp_output_path(self) -> Path:
    fd, raw_path = tempfile.mkstemp(prefix="pdf-craft-codex-", suffix=".txt")
    os.close(fd)
    return Path(raw_path)

  def _wrap_prompt(self, prompt: str, parser_name: str = "") -> str:
    format_instruction = self._format_instruction(parser_name)
    return (
      "You are acting as a text-only LLM backend for an application.\n"
      "Return only the requested answer content.\n"
      "Do not inspect files, do not run commands, and do not modify anything.\n"
      f"{format_instruction}\n\n"
      f"{prompt}"
    )

  def _format_instruction(self, parser_name: str) -> str:
    if parser_name == "_encode_xml":
      return (
        "Your response must be valid XML only.\n"
        "Do not output Markdown, explanations, bullet lists, or code fences.\n"
        "Return exactly one XML document rooted at <response>."
      )
    if parser_name == "_encode_json":
      return (
        "Your response must be valid JSON only.\n"
        "Do not output Markdown, explanations, or code fences."
      )
    if parser_name == "_encode_markdown":
      return (
        "Your response must be Markdown content only.\n"
        "Do not add prefatory explanations outside the requested Markdown."
      )
    return "If the prompt expects Markdown, JSON, or XML, output only that content."

  def _input2str(self, input: LanguageModelInput) -> str:
    if isinstance(input, str):
      return input
    if not isinstance(input, list):
      raise ValueError(f"Unsupported input type: {type(input)}")

    buffer = StringIO()
    is_first = True
    for message in input:
      if not is_first:
        buffer.write("\n\n")
      if isinstance(message, SystemMessage):
        buffer.write("System:\n")
        buffer.write(str(message.content))
      elif isinstance(message, HumanMessage):
        buffer.write("User:\n")
        buffer.write(str(message.content))
      elif isinstance(message, AIMessage):
        buffer.write("Assistant:\n")
        buffer.write(str(message.content))
      else:
        buffer.write(str(message))
      is_first = False
    return buffer.getvalue()
