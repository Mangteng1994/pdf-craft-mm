from __future__ import annotations

import os
import re
import shutil
import threading
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from conversion_service import convert_pdf_to_epub, ensure_runtime_dirs, resolve_path, runtime_paths
from llm_config import (
  ENV_PATH,
  ROOT_DIR,
  LLM_MODE_API_KEY,
  _load_env_file,
  find_codex_cli_path,
  validate_codex_cli_path,
)


STATIC_DIR = ROOT_DIR / "web_static"

CONFIG_KEYS = [
  "PDF_CRAFT_LLM_MODE",
  "PDF_CRAFT_LLM_API_KEY",
  "PDF_CRAFT_CODEX_CLI_PATH",
  "PDF_CRAFT_LLM_BASE_URL",
  "PDF_CRAFT_LLM_MODEL",
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
  "PDF_CRAFT_LLM_MODEL": "deepseek-chat",
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


class ConfigUpdate(BaseModel):
  values: dict[str, str]


class JobStart(BaseModel):
  pdf_name: str
  output_name: str | None = None


class JobDelete(BaseModel):
  pdf_name: str
  output_name: str | None = None


@dataclass
class JobState:
  status: str = "idle"
  pdf_name: str | None = None
  step: str = ""
  progress_current: int = 0
  progress_total: int | None = None
  output_file: str | None = None
  error: str | None = None
  started_at: str | None = None
  finished_at: str | None = None
  logs: list[str] = field(default_factory=list)


app = FastAPI(title="PDF Craft Local Web")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_job = JobState()
_job_lock = threading.Lock()
_worker: threading.Thread | None = None


def _timestamp() -> str:
  return datetime.now().strftime("%H:%M:%S")


def _append_log(message: str) -> None:
  with _job_lock:
    _job.logs.append(f"[{_timestamp()}] {message}")
    if len(_job.logs) > 500:
      del _job.logs[:len(_job.logs) - 500]


def _job_snapshot() -> dict[str, Any]:
  with _job_lock:
    return {
      "status": _job.status,
      "pdf_name": _job.pdf_name,
      "step": _job.step,
      "progress_current": _job.progress_current,
      "progress_total": _job.progress_total,
      "output_file": _job.output_file,
      "error": _job.error,
      "started_at": _job.started_at,
      "finished_at": _job.finished_at,
      "logs": list(_job.logs),
    }


def _read_env_values() -> dict[str, str]:
  values = dict(DEFAULT_CONFIG)
  if ENV_PATH.exists():
    for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
      line = raw_line.strip()
      if not line or line.startswith("#") or "=" not in line:
        continue
      key, value = line.split("=", 1)
      key = key.strip()
      if key.startswith("export "):
        key = key.removeprefix("export ").strip()
      if key in CONFIG_KEYS:
        values[key] = value.strip().strip('"').strip("'")
  return values


def _mask_key(value: str) -> str:
  if not value:
    return ""
  if len(value) <= 10:
    return "********"
  return f"{value[:6]}...{value[-4:]}"


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
    "PDF_CRAFT_LLM_MODEL={PDF_CRAFT_LLM_MODEL}",
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


def _input_pdf_path(pdf_name: str) -> Path:
  paths = runtime_paths()
  path = (paths.input_dir / Path(pdf_name).name).resolve()
  if paths.input_dir.resolve() not in path.parents:
    raise HTTPException(status_code=400, detail="PDF 路径不合法")
  if not path.exists():
    raise HTTPException(status_code=404, detail="PDF 文件不存在")
  return path


def _job_paths(pdf_name: str, output_name: str | None) -> tuple[Path, Path, Path, Path]:
  paths = ensure_runtime_dirs()
  pdf_path = (paths.input_dir / Path(pdf_name).name).resolve()
  if paths.input_dir.resolve() not in pdf_path.parents:
    raise HTTPException(status_code=400, detail="PDF 路径不合法")
  analysing_dir = (paths.work_dir / "analysing" / pdf_path.stem).resolve()
  output_dir = (paths.work_dir / "output" / pdf_path.stem).resolve()
  epub_path = (paths.dist_dir / Path(output_name or f"{pdf_path.stem}.epub").name).resolve()
  if paths.work_dir.resolve() not in analysing_dir.parents or paths.work_dir.resolve() not in output_dir.parents:
    raise HTTPException(status_code=400, detail="任务目录路径不合法")
  if paths.dist_dir.resolve() not in epub_path.parents:
    raise HTTPException(status_code=400, detail="输出文件路径不合法")
  return pdf_path, analysing_dir, output_dir, epub_path


def _run_job(pdf_path: Path, output_name: str | None) -> None:
  try:
    result = convert_pdf_to_epub(
      pdf_path=pdf_path,
      output_filename=output_name,
      log=_append_log,
      report_step=_set_step,
      report_progress=_set_progress,
    )
    with _job_lock:
      _job.status = "done"
      _job.output_file = result.epub_path.name
      _job.finished_at = datetime.now().isoformat(timespec="seconds")
  except Exception as err:
    _append_log(traceback.format_exc())
    with _job_lock:
      _job.status = "failed"
      _job.error = str(err)
      _job.finished_at = datetime.now().isoformat(timespec="seconds")


def _set_step(step: str) -> None:
  with _job_lock:
    _job.step = step
    _job.progress_current = 0
    _job.progress_total = None


def _set_progress(current: int, total: int | None) -> None:
  with _job_lock:
    _job.progress_current = current
    _job.progress_total = total


@app.get("/")
def index() -> FileResponse:
  return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/config")
def get_config() -> dict[str, Any]:
  _load_env_file()
  values = _read_env_values()
  masked = dict(values)
  masked["PDF_CRAFT_LLM_API_KEY"] = _mask_key(values.get("PDF_CRAFT_LLM_API_KEY", ""))
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
  global _worker
  with _job_lock:
    if _job.status == "running":
      raise HTTPException(status_code=409, detail="已有任务运行中")

  pdf_path = _input_pdf_path(payload.pdf_name)
  with _job_lock:
    _job.status = "running"
    _job.pdf_name = pdf_path.name
    _job.step = "准备开始"
    _job.progress_current = 0
    _job.progress_total = None
    _job.output_file = None
    _job.error = None
    _job.started_at = datetime.now().isoformat(timespec="seconds")
    _job.finished_at = None
    _job.logs = [f"[{_timestamp()}] 任务已启动：{pdf_path.name}"]

  _worker = threading.Thread(target=_run_job, args=(pdf_path, payload.output_name), daemon=True)
  _worker.start()
  return _job_snapshot()


@app.delete("/api/jobs")
def delete_job(payload: JobDelete) -> dict[str, Any]:
  with _job_lock:
    if _job.status == "running" and _job.pdf_name == Path(payload.pdf_name).name:
      raise HTTPException(status_code=409, detail="任务运行中，不能删除")

  pdf_path, analysing_dir, output_dir, epub_path = _job_paths(payload.pdf_name, payload.output_name)
  deleted_paths: list[str] = []

  for path in (pdf_path, analysing_dir, output_dir, epub_path):
    if not path.exists():
      continue
    if path.is_dir():
      shutil.rmtree(path)
    else:
      path.unlink()
    deleted_paths.append(str(path))

  with _job_lock:
    current_output = _job.output_file
    matches_current_job = _job.pdf_name == pdf_path.name
    matches_output = current_output == epub_path.name if current_output else False
    if matches_current_job or matches_output:
      _job.status = "idle"
      _job.pdf_name = None
      _job.step = "等待任务"
      _job.progress_current = 0
      _job.progress_total = None
      _job.output_file = None
      _job.error = None
      _job.started_at = None
      _job.finished_at = None
      _job.logs = [f"[{_timestamp()}] 已删除任务相关文件：{pdf_path.name}"]

  return {
    "deleted": deleted_paths,
    "message": "任务相关输入、输出和中间文件已删除",
    "pdfs": sorted(path.name for path in ensure_runtime_dirs().input_dir.glob("*.pdf")),
  }


@app.get("/api/jobs/current")
def current_job() -> dict[str, Any]:
  return _job_snapshot()


@app.get("/api/download/{filename}")
def download_epub(filename: str) -> FileResponse:
  paths = ensure_runtime_dirs()
  path = (paths.dist_dir / Path(filename).name).resolve()
  if paths.dist_dir.resolve() not in path.parents:
    raise HTTPException(status_code=400, detail="下载路径不合法")
  if path.suffix.lower() != ".epub" or not path.exists():
    raise HTTPException(status_code=404, detail="EPUB 文件不存在")
  return FileResponse(path, media_type="application/epub+zip", filename=path.name)
