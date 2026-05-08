from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
  from pdf_craft import LLM


ROOT_DIR = Path(__file__).resolve().parent
ENV_PATH = ROOT_DIR / ".env"
LLM_MODE_API_KEY = "api_key"
LLM_MODE_CODEX_CLI = "codex_cli"


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


def _codex_command_prefix(path: Path) -> list[str]:
  normalized = path
  if os.name == "nt" and normalized.suffix == "" and normalized.with_suffix(".cmd").exists():
    normalized = normalized.with_suffix(".cmd")

  suffix = normalized.suffix.lower()
  if suffix == ".ps1":
    return ["pwsh", "-NoProfile", "-File", str(normalized)]
  if suffix in (".cmd", ".bat"):
    return ["cmd.exe", "/c", str(normalized)]
  return [str(normalized)]


def validate_codex_cli_path(path_str: str) -> Path:
  candidate = Path(path_str).expanduser()
  if not candidate.is_absolute():
    candidate = (ROOT_DIR / candidate).resolve()
  else:
    candidate = candidate.resolve()

  if not candidate.exists():
    raise RuntimeError(f"Codex CLI not found: {candidate}")

  try:
    result = subprocess.run(
      _codex_command_prefix(candidate) + ["--version"],
      capture_output=True,
      text=True,
      encoding="utf-8",
      errors="replace",
      timeout=15,
      check=False,
    )
  except OSError as err:
    raise RuntimeError(f"Failed to execute Codex CLI: {candidate}") from err

  if result.returncode != 0:
    details = (result.stderr or result.stdout).strip()
    message = f"Codex CLI is not executable: {candidate}"
    if details:
      message = f"{message}. {details}"
    raise RuntimeError(message)

  return candidate


def _codex_cli_candidates() -> list[Path]:
  candidates: list[Path] = []

  for name in ("codex.exe", "codex.cmd", "codex", "codex.ps1"):
    found = shutil.which(name)
    if found:
      candidates.append(Path(found))

  if os.name == "nt":
    appdata = os.environ.get("APPDATA")
    local_appdata = os.environ.get("LOCALAPPDATA")
    program_files = os.environ.get("ProgramFiles")

    if appdata:
      npm_dir = Path(appdata) / "npm"
      candidates.extend([
        npm_dir / "codex",
        npm_dir / "codex.cmd",
        npm_dir / "codex.ps1",
      ])

    if local_appdata:
      candidates.append(Path(local_appdata) / "OpenAI" / "Codex" / "bin" / "codex.exe")

    if program_files:
      windows_apps = Path(program_files) / "WindowsApps"
      if windows_apps.exists():
        candidates.extend(sorted(windows_apps.glob("OpenAI.Codex_*\\app\\resources\\codex.exe")))
        candidates.extend(sorted(windows_apps.glob("OpenAI.Codex_*\\app\\resources\\codex")))

  return candidates


def find_codex_cli_path() -> Path | None:
  seen: set[str] = set()
  for candidate in _codex_cli_candidates():
    resolved = str(candidate.resolve())
    if resolved in seen:
      continue
    seen.add(resolved)
    try:
      return validate_codex_cli_path(resolved)
    except RuntimeError:
      continue
  return None


def create_llm() -> "LLM":
  from pdf_craft import LLM

  _load_env_file()

  log_dir = _get("PDF_CRAFT_LLM_LOG_DIR")
  log_dir_path = None if not log_dir else ROOT_DIR / log_dir
  mode = (_get("PDF_CRAFT_LLM_MODE", LLM_MODE_API_KEY) or LLM_MODE_API_KEY).strip().lower()

  if mode == LLM_MODE_CODEX_CLI:
    cli_path = _get("PDF_CRAFT_CODEX_CLI_PATH", required=True)
    validated_cli_path = validate_codex_cli_path(cli_path)
    return LLM(
      token_encoding=_get("PDF_CRAFT_TOKEN_ENCODING", "o200k_base") or "o200k_base",
      timeout=_get_float("PDF_CRAFT_LLM_TIMEOUT"),
      retry_times=_get_int("PDF_CRAFT_LLM_RETRY_TIMES", 5),
      retry_interval_seconds=_get_float("PDF_CRAFT_LLM_RETRY_INTERVAL_SECONDS") or 6.0,
      log_dir_path=log_dir_path,
      mode=LLM_MODE_CODEX_CLI,
      codex_cli_path=str(validated_cli_path),
    )

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
    mode=LLM_MODE_API_KEY,
  )
