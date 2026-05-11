from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from conversion_service import ensure_runtime_dirs
from job_queue import JobQueue
from llm_config import (
  CODEX_REASONING_EFFORTS,
  ENV_PATH,
  ROOT_DIR,
  LLM_MODE_API_KEY,
  _load_env_file,
  find_codex_cli_path,
  validate_codex_cli_path,
)


STATIC_DIR = ROOT_DIR / "web_static"
CODEX_HOME = Path.home() / ".codex"
CODEX_MODELS_CACHE_PATH = CODEX_HOME / "models_cache.json"

CONFIG_KEYS = [
  "PDF_CRAFT_LLM_MODE",
  "PDF_CRAFT_LLM_API_KEY",
  "PDF_CRAFT_CODEX_CLI_PATH",
  "PDF_CRAFT_LLM_BASE_URL",
  "PDF_CRAFT_API_MODEL",
  "PDF_CRAFT_CODEX_MODEL",
  "PDF_CRAFT_CODEX_MODEL_REASONING_EFFORT",
  "PDF_CRAFT_TOKEN_ENCODING",
  "PDF_CRAFT_PROXY_URL",
  "PDF_CRAFT_INPUT_DIR",
  "PDF_CRAFT_WORK_DIR",
  "PDF_CRAFT_DIST_DIR",
  "PDF_CRAFT_MODEL_DIR",
  "PDF_CRAFT_DEVICE",
  "PDF_CRAFT_EXTRACT_FORMULA",
  "PDF_CRAFT_EXTRACT_TABLE_FORMAT",
  "PDF_CRAFT_CORRECTION_MODE",
  "PDF_CRAFT_THREADS_COUNT",
  "PDF_CRAFT_WINDOW_TOKENS",
  "PDF_CRAFT_LLM_TIMEOUT",
  "PDF_CRAFT_LLM_RETRY_TIMES",
  "PDF_CRAFT_LLM_RETRY_INTERVAL_SECONDS",
  "PDF_CRAFT_LLM_TOP_P",
  "PDF_CRAFT_LLM_TEMPERATURE",
  "PDF_CRAFT_LLM_LOG_DIR",
]

DEFAULT_CONFIG = {
  "PDF_CRAFT_LLM_MODE": LLM_MODE_API_KEY,
  "PDF_CRAFT_LLM_API_KEY": "",
  "PDF_CRAFT_CODEX_CLI_PATH": "",
  "PDF_CRAFT_LLM_BASE_URL": "https://api.deepseek.com",
  "PDF_CRAFT_API_MODEL": "deepseek-chat",
  "PDF_CRAFT_CODEX_MODEL": "",
  "PDF_CRAFT_CODEX_MODEL_REASONING_EFFORT": "",
  "PDF_CRAFT_TOKEN_ENCODING": "o200k_base",
  "PDF_CRAFT_PROXY_URL": "",
  "PDF_CRAFT_INPUT_DIR": "inputs",
  "PDF_CRAFT_WORK_DIR": "work",
  "PDF_CRAFT_DIST_DIR": "dist",
  "PDF_CRAFT_MODEL_DIR": "models",
  "PDF_CRAFT_DEVICE": "cpu",
  "PDF_CRAFT_EXTRACT_FORMULA": "false",
  "PDF_CRAFT_EXTRACT_TABLE_FORMAT": "disable",
  "PDF_CRAFT_CORRECTION_MODE": "no",
  "PDF_CRAFT_THREADS_COUNT": "1",
  "PDF_CRAFT_WINDOW_TOKENS": "",
  "PDF_CRAFT_LLM_TIMEOUT": "120",
  "PDF_CRAFT_LLM_RETRY_TIMES": "5",
  "PDF_CRAFT_LLM_RETRY_INTERVAL_SECONDS": "6",
  "PDF_CRAFT_LLM_TOP_P": "0.8",
  "PDF_CRAFT_LLM_TEMPERATURE": "0.3",
  "PDF_CRAFT_LLM_LOG_DIR": "logs/llm",
}

LEGACY_MODEL_KEY = "PDF_CRAFT_LLM_MODEL"


class ConfigUpdate(BaseModel):
  values: dict[str, str]


class JobStart(BaseModel):
  pdf_name: str
  output_name: str | None = None


class JobDelete(BaseModel):
  pdf_name: str
  output_name: str | None = None


class JobAction(BaseModel):
  job_id: str


class JobDeleteById(BaseModel):
  job_id: str
  delete_files: bool = True


class PreflightItem(BaseModel):
  id: str
  label: str
  status: str
  message: str
  blocking: bool = False


class PreflightCommand(BaseModel):
  label: str
  command: str


class PreflightGuide(BaseModel):
  id: str
  title: str
  status: str
  summary: str
  details: list[str] = []
  commands: list[dict[str, str]] = []


app = FastAPI(title="PDF Craft Local Web")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_jobs = JobQueue()


def _read_env_values() -> dict[str, str]:
  values = dict(DEFAULT_CONFIG)
  legacy_model = ""
  if ENV_PATH.exists():
    for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
      line = raw_line.strip()
      if not line or line.startswith("#") or "=" not in line:
        continue
      key, value = line.split("=", 1)
      key = key.strip()
      if key.startswith("export "):
        key = key.removeprefix("export ").strip()
      if key == LEGACY_MODEL_KEY:
        legacy_model = value.strip().strip('"').strip("'")
      elif key in CONFIG_KEYS:
        values[key] = value.strip().strip('"').strip("'")
  if legacy_model:
    mode = values.get("PDF_CRAFT_LLM_MODE", LLM_MODE_API_KEY).strip().lower()
    if mode == LLM_MODE_API_KEY and not values.get("PDF_CRAFT_API_MODEL", "").strip():
      values["PDF_CRAFT_API_MODEL"] = legacy_model
    if mode != LLM_MODE_API_KEY and not values.get("PDF_CRAFT_CODEX_MODEL", "").strip():
      values["PDF_CRAFT_CODEX_MODEL"] = legacy_model
  return values


def _mask_key(value: str) -> str:
  if not value:
    return ""
  if len(value) <= 10:
    return "********"
  return f"{value[:6]}...{value[-4:]}"


def _load_codex_models() -> list[dict[str, Any]]:
  if not CODEX_MODELS_CACHE_PATH.exists():
    return []

  try:
    payload = json.loads(CODEX_MODELS_CACHE_PATH.read_text(encoding="utf-8"))
  except (OSError, json.JSONDecodeError):
    return []

  models = payload.get("models")
  if not isinstance(models, list):
    return []

  result: list[dict[str, Any]] = []
  for item in models:
    if not isinstance(item, dict):
      continue
    slug = str(item.get("slug", "")).strip()
    if not slug:
      continue
    visibility = str(item.get("visibility", "list")).strip().lower()
    if visibility not in ("list", "featured", "default"):
      continue
    display_name = str(item.get("display_name", slug)).strip() or slug
    result.append({
      "slug": slug,
      "display_name": display_name,
      "description": str(item.get("description", "")).strip(),
      "default_reasoning_level": str(item.get("default_reasoning_level", "")).strip(),
      "supported_reasoning_levels": [
        str(level.get("effort", "")).strip()
        for level in item.get("supported_reasoning_levels", [])
        if isinstance(level, dict) and str(level.get("effort", "")).strip()
      ],
      "priority": int(item.get("priority", 9999)) if str(item.get("priority", "")).strip() else 9999,
    })

  result.sort(key=lambda item: (item["priority"], item["display_name"].lower()))
  return result


def _write_env_values(values: dict[str, str]) -> None:
  final_values = dict(DEFAULT_CONFIG)
  final_values.update(_read_env_values())
  final_values.update({key: str(value) for key, value in values.items() if key in CONFIG_KEYS})

  current_key = _read_env_values().get("PDF_CRAFT_LLM_API_KEY", "")
  submitted_key = values.get("PDF_CRAFT_LLM_API_KEY", "")
  if submitted_key == "" or "*" in submitted_key or "..." in submitted_key:
    final_values["PDF_CRAFT_LLM_API_KEY"] = current_key

  lines = [
    "PDF_CRAFT_LLM_MODE={PDF_CRAFT_LLM_MODE}",
    "PDF_CRAFT_LLM_API_KEY={PDF_CRAFT_LLM_API_KEY}",
    "PDF_CRAFT_CODEX_CLI_PATH={PDF_CRAFT_CODEX_CLI_PATH}",
    "PDF_CRAFT_LLM_BASE_URL={PDF_CRAFT_LLM_BASE_URL}",
    "PDF_CRAFT_API_MODEL={PDF_CRAFT_API_MODEL}",
    "PDF_CRAFT_CODEX_MODEL={PDF_CRAFT_CODEX_MODEL}",
    "PDF_CRAFT_CODEX_MODEL_REASONING_EFFORT={PDF_CRAFT_CODEX_MODEL_REASONING_EFFORT}",
    "PDF_CRAFT_TOKEN_ENCODING={PDF_CRAFT_TOKEN_ENCODING}",
    "PDF_CRAFT_PROXY_URL={PDF_CRAFT_PROXY_URL}",
    "",
    "# Runtime paths",
    "PDF_CRAFT_INPUT_DIR={PDF_CRAFT_INPUT_DIR}",
    "PDF_CRAFT_WORK_DIR={PDF_CRAFT_WORK_DIR}",
    "PDF_CRAFT_DIST_DIR={PDF_CRAFT_DIST_DIR}",
    "PDF_CRAFT_MODEL_DIR={PDF_CRAFT_MODEL_DIR}",
    "",
    "# OCR/runtime settings",
    "PDF_CRAFT_DEVICE={PDF_CRAFT_DEVICE}",
    "PDF_CRAFT_EXTRACT_FORMULA={PDF_CRAFT_EXTRACT_FORMULA}",
    "PDF_CRAFT_EXTRACT_TABLE_FORMAT={PDF_CRAFT_EXTRACT_TABLE_FORMAT}",
    "PDF_CRAFT_CORRECTION_MODE={PDF_CRAFT_CORRECTION_MODE}",
    "PDF_CRAFT_THREADS_COUNT={PDF_CRAFT_THREADS_COUNT}",
    "PDF_CRAFT_WINDOW_TOKENS={PDF_CRAFT_WINDOW_TOKENS}",
    "",
    "# Optional settings",
    "PDF_CRAFT_LLM_TIMEOUT={PDF_CRAFT_LLM_TIMEOUT}",
    "PDF_CRAFT_LLM_RETRY_TIMES={PDF_CRAFT_LLM_RETRY_TIMES}",
    "PDF_CRAFT_LLM_RETRY_INTERVAL_SECONDS={PDF_CRAFT_LLM_RETRY_INTERVAL_SECONDS}",
    "PDF_CRAFT_LLM_TOP_P={PDF_CRAFT_LLM_TOP_P}",
    "PDF_CRAFT_LLM_TEMPERATURE={PDF_CRAFT_LLM_TEMPERATURE}",
    "PDF_CRAFT_LLM_LOG_DIR={PDF_CRAFT_LLM_LOG_DIR}",
  ]
  ENV_PATH.write_text("\n".join(line.format(**final_values) for line in lines) + "\n", encoding="utf-8")

  for key, value in final_values.items():
    os.environ[key] = value

  proxy = final_values.get("PDF_CRAFT_PROXY_URL", "").strip()
  proxy_keys = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
  if proxy:
    for key in proxy_keys:
      os.environ[key] = proxy
  else:
    for key in proxy_keys:
      os.environ.pop(key, None)


def _safe_pdf_name(filename: str) -> str:
  name = Path(filename).name
  stem = Path(name).stem
  suffix = Path(name).suffix.lower()
  if suffix != ".pdf":
    raise HTTPException(status_code=400, detail="只能上传 PDF 文件")
  stem = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", stem).strip("._")
  if not stem:
    stem = "uploaded"
  return f"{stem}.pdf"


def _unique_path(directory: Path, filename: str) -> Path:
  candidate = directory / filename
  if not candidate.exists():
    return candidate
  stem = candidate.stem
  suffix = candidate.suffix
  for index in range(1, 1000):
    next_candidate = directory / f"{stem}_{index}{suffix}"
    if not next_candidate.exists():
      return next_candidate
  raise HTTPException(status_code=409, detail="无法生成唯一文件名")


def _preflight_item(
    item_id: str,
    label: str,
    status: str,
    message: str,
    blocking: bool = False,
  ) -> dict[str, Any]:
  return PreflightItem(
    id=item_id,
    label=label,
    status=status,
    message=message,
    blocking=blocking,
  ).model_dump()


def _preflight_command(label: str, command: str) -> dict[str, str]:
  return PreflightCommand(label=label, command=command).model_dump()


def _package_version(name: str) -> str | None:
  try:
    return package_version(name)
  except PackageNotFoundError:
    return None


def _runtime_info() -> dict[str, Any]:
  providers: list[str] = []
  try:
    import onnxruntime as ort  # pylint: disable=import-outside-toplevel
    providers = list(ort.get_available_providers())
  except ImportError:
    providers = []

  return {
    "python": sys.executable,
    "onnxruntime": _package_version("onnxruntime"),
    "onnxruntime_gpu": _package_version("onnxruntime-gpu"),
    "providers": providers,
  }


def _runtime_flavor_from_path(python_path: str) -> str | None:
  normalized = python_path.replace("\\", "/").lower()
  if "/.venvs/cpu/" in normalized:
    return "cpu"
  if "/.venvs/gpu/" in normalized:
    return "gpu"
  return None


def _runtime_commands(flavor: str) -> list[dict[str, str]]:
  return [
    _preflight_command(f"创建 {flavor.upper()} 环境", f"python scripts/runtime_env.py create {flavor}"),
    _preflight_command(f"用 {flavor.upper()} 环境启动 Web", f"python scripts/runtime_env.py run-web {flavor} --reload"),
    _preflight_command(f"检查 {flavor.upper()} 环境", f"python scripts/runtime_env.py check {flavor}"),
  ]


def _latex_commands() -> list[dict[str, str]]:
  commands: list[dict[str, str]] = []
  if os.name == "nt":
    if shutil.which("winget"):
      commands.append(_preflight_command("安装 MiKTeX", "winget install MiKTeX.MiKTeX"))
    commands.append(_preflight_command("检查 latex 命令", "where latex"))
    return commands

  commands.append(_preflight_command("检查 latex 命令", "which latex"))
  return commands


def _build_runtime_guides(values: dict[str, str], runtime: dict[str, Any]) -> list[dict[str, Any]]:
  selected_device = values.get("PDF_CRAFT_DEVICE", "cpu").lower()
  formula_enabled = values.get("PDF_CRAFT_EXTRACT_FORMULA", "false").lower() in ("1", "true", "yes", "on")
  latex_path = shutil.which("latex")
  providers = runtime["providers"]
  providers_text = ", ".join(providers) if providers else "未检测到 onnxruntime provider"
  python_path = runtime["python"]
  runtime_flavor = _runtime_flavor_from_path(python_path)
  flavor_label = {
    "cpu": "CPU",
    "gpu": "GPU",
  }.get(runtime_flavor, "当前解释器")

  cpu_status = "active" if selected_device == "cpu" else "ready"
  cuda_ready = "CUDAExecutionProvider" in providers
  cuda_status = "active" if selected_device == "cuda" and cuda_ready else "attention" if selected_device == "cuda" else "ready"
  latex_status = "attention" if formula_enabled and not latex_path else "active" if formula_enabled else "optional"

  cpu_guide = PreflightGuide(
    id="cpu",
    title="CPU 模式",
    status=cpu_status,
    summary="兼容性最好，不依赖 NVIDIA 或 CUDA。适合先跑通整条链路。",
    details=[
      "如果只想稳定完成 OCR 和文本纠错，优先用 CPU 模式。",
      f"当前 Web 服务解释器：{python_path}",
      f"当前 provider：{providers_text}",
      "切换到 CPU 不是只改下拉框，而是要用 CPU 环境重新启动 Web 服务。",
    ],
    commands=_runtime_commands("cpu"),
  ).model_dump()

  cuda_details = [
    "CUDA 模式要求当前 Web 服务运行在 GPU 环境里，并且 onnxruntime 能加载 CUDAExecutionProvider。",
    f"当前 Web 服务解释器：{python_path}",
    f"当前 provider：{providers_text}",
  ]
  if runtime["onnxruntime_gpu"]:
    cuda_details.append(f"已安装 onnxruntime-gpu {runtime['onnxruntime_gpu']}。")
  else:
    cuda_details.append("当前解释器里没有检测到 onnxruntime-gpu。")
  if not cuda_ready:
    cuda_details.append("仅在页面里切到 CUDA，不会替换后台 Python 环境；需要用 GPU 环境重启 Web 服务。")
    if runtime_flavor == "cpu":
      cuda_details.append("当前服务明显跑在 CPU 环境里，所以预检会阻断 CUDA。")
    elif runtime["onnxruntime_gpu"]:
      cuda_details.append("如果已经是 GPU 环境但仍没有 CUDA provider，继续检查 NVIDIA 驱动或 CUDA/cuDNN。")

  cuda_guide = PreflightGuide(
    id="cuda",
    title="CUDA 模式",
    status=cuda_status,
    summary="速度更快，但要求 GPU 依赖、驱动和当前服务进程都切到 GPU 环境。",
    details=cuda_details,
    commands=_runtime_commands("gpu"),
  ).model_dump()

  latex_details = [
    "LaTeX 只在开启公式识别时才需要，用于公式 SVG 渲染。",
    "安装后需要重新打开终端，并重新启动当前 Web 服务，让 latex 进入 PATH。",
  ]
  if latex_path:
    latex_details.insert(1, f"当前已检测到 latex：{latex_path}")
  elif formula_enabled:
    latex_details.insert(1, "当前已开启公式识别，但系统 PATH 里没有 latex 命令。")
  else:
    latex_details.insert(1, "当前未开启公式识别，可以先不安装。")

  latex_guide = PreflightGuide(
    id="latex",
    title="LaTeX 公式环境",
    status=latex_status,
    summary="安装后可用于公式渲染；没装时，公式相关能力会退化或不可用。",
    details=latex_details,
    commands=_latex_commands(),
  ).model_dump()

  return [cpu_guide, cuda_guide, latex_guide]


def _resolve_config_path(value: str) -> Path:
  path = Path(value)
  return path if path.is_absolute() else ROOT_DIR / path


def _check_writable_dir(value: str, label: str, item_id: str) -> dict[str, Any]:
  path = _resolve_config_path(value)
  try:
    path.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path, prefix=".pdf-craft-check-", delete=True):
      pass
    return _preflight_item(item_id, label, "ok", f"可写：{path}")
  except OSError as err:
    return _preflight_item(item_id, label, "error", f"不可写：{path}。{err}", True)


def _check_http_reachable(url: str, proxy: str) -> dict[str, Any]:
  parsed = urllib.parse.urlparse(url)
  if parsed.scheme not in ("http", "https") or not parsed.netloc:
    return _preflight_item("llm_url", "LLM 服务地址", "error", "服务地址必须是 http 或 https URL", True)

  opener = urllib.request.build_opener()
  if proxy:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({
      "http": proxy,
      "https": proxy,
    }))
  request = urllib.request.Request(url, method="HEAD")
  try:
    with opener.open(request, timeout=5):
      return _preflight_item("llm_url", "LLM 服务地址", "ok", "服务地址可连接")
  except urllib.error.HTTPError as err:
    if err.code < 500:
      return _preflight_item("llm_url", "LLM 服务地址", "ok", f"服务有响应：HTTP {err.code}")
    return _preflight_item("llm_url", "LLM 服务地址", "warn", f"服务返回 HTTP {err.code}")
  except (OSError, urllib.error.URLError) as err:
    return _preflight_item("llm_url", "LLM 服务地址", "warn", f"暂时无法连接服务地址：{err}")


def _check_cuda() -> dict[str, Any]:
  runtime = _runtime_info()
  providers = runtime["providers"]
  if not providers:
    return _preflight_item("cuda", "CUDA 运行环境", "error", "未安装 onnxruntime，无法使用 CUDA", True)

  if "CUDAExecutionProvider" in providers:
    return _preflight_item("cuda", "CUDA 运行环境", "ok", "检测到 CUDAExecutionProvider")
  return _preflight_item("cuda", "CUDA 运行环境", "error", "当前环境没有 CUDAExecutionProvider，请改用 CPU 或安装 GPU 依赖", True)


def _check_integer(value: str, label: str, item_id: str, minimum: int) -> dict[str, Any]:
  try:
    parsed = int(value)
  except ValueError:
    return _preflight_item(item_id, label, "error", "必须是整数", True)
  if parsed < minimum:
    return _preflight_item(item_id, label, "error", f"不能小于 {minimum}", True)
  return _preflight_item(item_id, label, "ok", f"当前值：{parsed}")


def _check_float(value: str, label: str, item_id: str, minimum: float, maximum: float | None = None) -> dict[str, Any]:
  try:
    parsed = float(value)
  except ValueError:
    return _preflight_item(item_id, label, "error", "必须是数字", True)
  if parsed < minimum:
    return _preflight_item(item_id, label, "error", f"不能小于 {minimum:g}", True)
  if maximum is not None and parsed > maximum:
    return _preflight_item(item_id, label, "error", f"不能大于 {maximum:g}", True)
  return _preflight_item(item_id, label, "ok", f"当前值：{parsed:g}")


def _run_preflight(pdf_name: str | None = None, output_name: str | None = None) -> dict[str, Any]:
  _load_env_file()
  values = _read_env_values()
  runtime = _runtime_info()
  items: list[dict[str, Any]] = []

  mode = values.get("PDF_CRAFT_LLM_MODE", LLM_MODE_API_KEY).strip().lower()
  if mode == LLM_MODE_API_KEY:
    api_key = values.get("PDF_CRAFT_LLM_API_KEY", "").strip()
    if not api_key or api_key == "sk-REPLACE_WITH_YOUR_KEY":
      items.append(_preflight_item("llm_key", "API Key", "error", "API Key 未配置", True))
    else:
      items.append(_preflight_item("llm_key", "API Key", "ok", "API Key 已配置"))
    items.append(_check_http_reachable(
      values.get("PDF_CRAFT_LLM_BASE_URL", ""),
      values.get("PDF_CRAFT_PROXY_URL", "").strip(),
    ))
  elif mode == "codex_cli":
    cli_path = values.get("PDF_CRAFT_CODEX_CLI_PATH", "").strip()
    if not cli_path:
      items.append(_preflight_item("codex_cli", "Codex CLI", "error", "未配置 Codex CLI 路径", True))
    else:
      try:
        validated = validate_codex_cli_path(cli_path)
        items.append(_preflight_item("codex_cli", "Codex CLI", "ok", f"可执行：{validated}"))
      except RuntimeError as err:
        items.append(_preflight_item("codex_cli", "Codex CLI", "error", str(err), True))
    codex_model = values.get("PDF_CRAFT_CODEX_MODEL", "").strip()
    if codex_model:
      items.append(_preflight_item("codex_model", "Codex 模型", "ok", f"当前模型：{codex_model}"))
    else:
      items.append(_preflight_item("codex_model", "Codex 模型", "warn", "未单独指定模型，将沿用 Codex 默认模型"))

    reasoning_effort = values.get("PDF_CRAFT_CODEX_MODEL_REASONING_EFFORT", "").strip().lower()
    if not reasoning_effort:
      items.append(_preflight_item("codex_reasoning", "思考程度", "ok", "未单独指定，将沿用 Codex 配置文件中的 model_reasoning_effort"))
    elif reasoning_effort in CODEX_REASONING_EFFORTS:
      items.append(_preflight_item("codex_reasoning", "思考程度", "ok", f"当前强制为：{reasoning_effort}"))
    else:
      items.append(_preflight_item(
        "codex_reasoning",
        "思考程度",
        "error",
        "只支持 low、medium、high、xhigh",
        True,
      ))
  else:
    items.append(_preflight_item("llm_mode", "LLM 接入方式", "error", f"不支持的接入方式：{mode}", True))

  for key, label, item_id in (
      ("PDF_CRAFT_INPUT_DIR", "源 PDF 目录", "input_dir"),
      ("PDF_CRAFT_WORK_DIR", "工作目录", "work_dir"),
      ("PDF_CRAFT_DIST_DIR", "输出目录", "dist_dir"),
      ("PDF_CRAFT_MODEL_DIR", "本地模型目录", "model_dir"),
    ):
    items.append(_check_writable_dir(values.get(key, DEFAULT_CONFIG[key]), label, item_id))

  if values.get("PDF_CRAFT_DEVICE", "cpu").lower() == "cuda":
    items.append(_check_cuda())
  else:
    items.append(_preflight_item("device", "运行设备", "ok", "当前使用 CPU"))

  table_format = values.get("PDF_CRAFT_EXTRACT_TABLE_FORMAT", "disable").lower()
  if table_format not in ("disable", "html", "markdown", "latex", "auto"):
    items.append(_preflight_item("table_format", "表格识别", "error", f"不支持的格式：{table_format}", True))
  elif table_format != "disable" and values.get("PDF_CRAFT_DEVICE", "cpu").lower() != "cuda":
    items.append(_preflight_item("table_format", "表格识别", "warn", "表格结构识别通常需要 CUDA，CPU 下可能回退或失败"))
  else:
    items.append(_preflight_item("table_format", "表格识别", "ok", f"当前模式：{table_format}"))

  if values.get("PDF_CRAFT_EXTRACT_FORMULA", "false").lower() in ("1", "true", "yes", "on"):
    latex_path = shutil.which("latex")
    if latex_path:
      items.append(_preflight_item("latex", "LaTeX", "ok", f"已检测到：{latex_path}"))
    else:
      items.append(_preflight_item("latex", "LaTeX", "warn", "未检测到 latex 命令，公式 SVG 渲染可能不可用"))
  else:
    items.append(_preflight_item("latex", "LaTeX", "ok", "未开启公式识别"))

  items.append(_check_integer(values.get("PDF_CRAFT_THREADS_COUNT", "1"), "线程数", "threads_count", 1))
  items.append(_check_integer(values.get("PDF_CRAFT_LLM_RETRY_TIMES", "5"), "重试次数", "retry_times", 0))
  items.append(_check_float(values.get("PDF_CRAFT_LLM_RETRY_INTERVAL_SECONDS", "6"), "重试间隔", "retry_interval", 0))
  items.append(_check_float(values.get("PDF_CRAFT_LLM_TOP_P", "0.8"), "Top P", "top_p", 0, 1))
  items.append(_check_float(values.get("PDF_CRAFT_LLM_TEMPERATURE", "0.3"), "温度", "temperature", 0, 2))

  if pdf_name:
    paths = ensure_runtime_dirs()
    pdf_path = (paths.input_dir / Path(pdf_name).name).resolve()
    if paths.input_dir.resolve() not in pdf_path.parents or not pdf_path.exists():
      items.append(_preflight_item("source_pdf", "源 PDF", "error", "请选择存在的 PDF 文件", True))
    else:
      items.append(_preflight_item("source_pdf", "源 PDF", "ok", f"已选择：{pdf_path.name}"))
      output_file = output_name if output_name else f"{pdf_path.stem}.epub"
      output_file = output_file if output_file.lower().endswith(".epub") else f"{output_file}.epub"
      epub_path = (paths.dist_dir / Path(output_file).name).resolve()
      if epub_path.exists():
        items.append(_preflight_item("output_file", "输出文件", "warn", f"{epub_path.name} 已存在，新任务会覆盖同名产物"))
      else:
        items.append(_preflight_item("output_file", "输出文件", "ok", f"将输出：{epub_path.name}"))
  else:
    items.append(_preflight_item("source_pdf", "源 PDF", "warn", "尚未选择 PDF"))

  blocking_errors = [item for item in items if item["blocking"] and item["status"] == "error"]
  warnings = [item for item in items if item["status"] == "warn"]
  return {
    "can_start": not blocking_errors,
    "summary": {
      "errors": len(blocking_errors),
      "warnings": len(warnings),
      "total": len(items),
    },
    "items": items,
    "runtime": runtime,
    "guides": _build_runtime_guides(values, runtime),
  }


@app.get("/")
def index() -> FileResponse:
  return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/config")
def get_config() -> dict[str, Any]:
  _load_env_file()
  values = _read_env_values()
  masked = dict(values)
  masked["PDF_CRAFT_LLM_API_KEY"] = _mask_key(values.get("PDF_CRAFT_LLM_API_KEY", ""))
  codex_models = _load_codex_models()
  configured_model = values.get("PDF_CRAFT_CODEX_MODEL", "").strip()
  if configured_model and not any(item["slug"] == configured_model for item in codex_models):
    codex_models.insert(0, {
      "slug": configured_model,
      "display_name": configured_model,
      "description": "",
      "default_reasoning_level": "",
      "supported_reasoning_levels": [],
      "priority": -1,
    })
  codex_cli_path = values.get("PDF_CRAFT_CODEX_CLI_PATH", "").strip()
  codex_cli_available = False
  if codex_cli_path:
    try:
      validate_codex_cli_path(codex_cli_path)
      codex_cli_available = True
    except RuntimeError:
      codex_cli_available = False
  paths = ensure_runtime_dirs()
  pdfs = sorted(path.name for path in paths.input_dir.glob("*.pdf"))
  return {
    "values": masked,
    "has_api_key": bool(values.get("PDF_CRAFT_LLM_API_KEY")),
    "codex_cli_available": codex_cli_available,
    "codex_models": codex_models,
    "pdfs": pdfs,
  }


@app.post("/api/config")
def save_config(payload: ConfigUpdate) -> dict[str, Any]:
  _write_env_values(payload.values)
  ensure_runtime_dirs()
  return get_config()


@app.post("/api/config/detect-codex-cli")
def detect_codex_cli() -> dict[str, str]:
  detected = find_codex_cli_path()
  if detected is None:
    raise HTTPException(status_code=404, detail="未检测到可用的 Codex CLI，请先在本机安装并确保命令可执行。")
  return {"path": str(detected)}


@app.get("/api/preflight")
def preflight(pdf_name: str | None = None, output_name: str | None = None) -> dict[str, Any]:
  return _run_preflight(pdf_name, output_name)


@app.post("/api/upload")
def upload_pdf(file: UploadFile = File(...)) -> dict[str, str]:
  paths = ensure_runtime_dirs()
  filename = _safe_pdf_name(file.filename or "uploaded.pdf")
  target = _unique_path(paths.input_dir, filename)
  with target.open("wb") as output:
    shutil.copyfileobj(file.file, output)
  return {"name": target.name}


@app.post("/api/jobs")
def start_job(payload: JobStart) -> dict[str, Any]:
  result = _run_preflight(payload.pdf_name, payload.output_name)
  if not result["can_start"]:
    raise HTTPException(status_code=409, detail={
      "message": "预检未通过，任务未加入队列",
      "preflight": result,
    })
  return _jobs.enqueue(payload.pdf_name, payload.output_name)


@app.delete("/api/jobs")
def delete_job(payload: JobDelete) -> dict[str, Any]:
  result = _jobs.delete_by_pdf(payload.pdf_name, payload.output_name)
  result["message"] = "任务相关输入、输出和中间文件已删除"
  result["pdfs"] = sorted(path.name for path in ensure_runtime_dirs().input_dir.glob("*.pdf"))
  return result


@app.get("/api/jobs")
def list_jobs() -> dict[str, Any]:
  _jobs.start_worker()
  return {"jobs": _jobs.list_jobs()}


@app.post("/api/jobs/pause")
def pause_job(payload: JobAction) -> dict[str, Any]:
  return _jobs.pause(payload.job_id)


@app.post("/api/jobs/resume")
def resume_job(payload: JobAction) -> dict[str, Any]:
  return _jobs.resume(payload.job_id)


@app.post("/api/jobs/retry")
def retry_job(payload: JobAction) -> dict[str, Any]:
  return _jobs.retry(payload.job_id)


@app.post("/api/jobs/cancel")
def cancel_job(payload: JobAction) -> dict[str, Any]:
  return _jobs.cancel(payload.job_id)


@app.post("/api/jobs/delete")
def delete_job_by_id(payload: JobDeleteById) -> dict[str, Any]:
  return _jobs.delete(payload.job_id, payload.delete_files)


@app.get("/api/jobs/current")
def current_job() -> dict[str, Any]:
  _jobs.start_worker()
  return _jobs.current_job()


@app.get("/api/download/{filename}")
def download_epub(filename: str) -> FileResponse:
  paths = ensure_runtime_dirs()
  path = (paths.dist_dir / Path(filename).name).resolve()
  if paths.dist_dir.resolve() not in path.parents:
    raise HTTPException(status_code=400, detail="下载路径不合法")
  if path.suffix.lower() != ".epub" or not path.exists():
    raise HTTPException(status_code=404, detail="EPUB 文件不存在")
  return FileResponse(path, media_type="application/epub+zip", filename=path.name)
