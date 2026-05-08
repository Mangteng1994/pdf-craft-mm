from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
  from pdf_craft import LLM


ROOT_DIR = Path(__file__).resolve().parent
ENV_PATH = ROOT_DIR / ".env"


def _load_env_file(path: Path = ENV_PATH) -> None:
  if not path.exists():
    return

  for raw_line in path.read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
      continue

    key, value = line.split("=", 1)
    key = key.strip()
    value = value.strip().strip('"').strip("'")
    if key.startswith("export "):
      key = key.removeprefix("export ").strip()
    os.environ.setdefault(key, value)

  proxy = os.environ.get("PDF_CRAFT_PROXY_URL", "").strip()
  proxy_keys = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
  if proxy:
    for key in proxy_keys:
      os.environ.setdefault(key, proxy)


def _get(name: str, default: str | None = None, required: bool = False) -> str | None:
  value = os.environ.get(name, default)
  if required and (not value or value == "sk-REPLACE_WITH_YOUR_KEY"):
    raise RuntimeError(f"Please set {name} in {ENV_PATH}")
  return value


def _get_float(name: str) -> float | None:
  value = _get(name)
  return None if value in (None, "") else float(value)


def _get_int(name: str, default: int) -> int:
  value = _get(name)
  return default if value in (None, "") else int(value)


def create_llm() -> "LLM":
  from pdf_craft import LLM

  _load_env_file()

  log_dir = _get("PDF_CRAFT_LLM_LOG_DIR")
  log_dir_path = None if not log_dir else ROOT_DIR / log_dir

  return LLM(
    key=_get("PDF_CRAFT_LLM_API_KEY", required=True),
    url=_get("PDF_CRAFT_LLM_BASE_URL", "https://api.deepseek.com"),
    model=_get("PDF_CRAFT_LLM_MODEL", "deepseek-chat"),
    token_encoding=_get("PDF_CRAFT_TOKEN_ENCODING", "o200k_base"),
    timeout=_get_float("PDF_CRAFT_LLM_TIMEOUT"),
    top_p=_get_float("PDF_CRAFT_LLM_TOP_P"),
    temperature=_get_float("PDF_CRAFT_LLM_TEMPERATURE"),
    retry_times=_get_int("PDF_CRAFT_LLM_RETRY_TIMES", 5),
    retry_interval_seconds=_get_float("PDF_CRAFT_LLM_RETRY_INTERVAL_SECONDS") or 6.0,
    log_dir_path=log_dir_path,
  )
