from __future__ import annotations

import json
import re
import threading
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from conversion_service import (
  ConversionResult,
  RuntimePaths,
  convert_pdf_to_epub,
  ensure_runtime_dirs,
  output_name,
  runtime_paths,
)


TERMINAL_STATUSES = {"done", "failed", "canceled"}
ACTIVE_STATUSES = {"queued", "running", "pausing", "canceling"}
RESTART_PAUSED_STATUSES = {"running", "pausing", "canceling"}


class JobPaused(RuntimeError):
  pass


class JobCancelled(RuntimeError):
  pass


@dataclass
class ConversionJob:
  id: str
  pdf_name: str
  output_name: str | None
  status: str
  pdf_path: str
  analysing_dir: str
  output_dir: str
  epub_path: str
  step: str = "等待任务"
  progress_current: int = 0
  progress_total: int | None = None
  output_file: str | None = None
  error: str | None = None
  created_at: str = ""
  updated_at: str = ""
  started_at: str | None = None
  finished_at: str | None = None
  logs: list[str] = field(default_factory=list)


def _now() -> str:
  return datetime.now().isoformat(timespec="seconds")


def _clock() -> str:
  return datetime.now().strftime("%H:%M:%S")


def _safe_stem(value: str) -> str:
  stem = Path(value).stem
  stem = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", stem).strip("._")
  return stem or "document"


def _safe_output_name(pdf_path: Path, raw_output_name: str | None) -> str:
  candidate = output_name(pdf_path, raw_output_name)
  candidate = Path(candidate).name
  if not candidate.lower().endswith(".epub"):
    candidate = f"{candidate}.epub"
  return candidate


class JobQueue:
  def __init__(self) -> None:
    self._lock = threading.RLock()
    self._worker: threading.Thread | None = None
    self._jobs: dict[str, ConversionJob] = {}
    self._jobs_dir: Path | None = None
    self._load_jobs()

  def list_jobs(self) -> list[dict[str, Any]]:
    self._load_jobs()
    with self._lock:
      jobs = sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)
      return [self._snapshot(job) for job in jobs]

  def get_job(self, job_id: str) -> dict[str, Any]:
    self._load_jobs()
    with self._lock:
      return self._snapshot(self._require_job(job_id))

  def current_job(self) -> dict[str, Any]:
    self._load_jobs()
    with self._lock:
      running = [job for job in self._jobs.values() if job.status in ("running", "pausing", "canceling")]
      if running:
        return self._snapshot(sorted(running, key=lambda job: job.started_at or job.created_at)[0])
      queued = [job for job in self._jobs.values() if job.status == "queued"]
      if queued:
        return self._snapshot(sorted(queued, key=lambda job: job.created_at)[0])
      jobs = sorted(self._jobs.values(), key=lambda job: job.updated_at or job.created_at, reverse=True)
      if jobs:
        return self._snapshot(jobs[0])
      return {
        "id": None,
        "status": "idle",
        "pdf_name": None,
        "step": "等待任务",
        "progress_current": 0,
        "progress_total": None,
        "output_file": None,
        "error": None,
        "started_at": None,
        "finished_at": None,
        "logs": [],
      }

  def enqueue(self, pdf_name: str, output_name_value: str | None) -> dict[str, Any]:
    paths = ensure_runtime_dirs()
    pdf_path = self._input_pdf_path(paths, pdf_name)
    created_at = _now()
    job_id = f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{_safe_stem(pdf_path.name)}-{uuid4().hex[:8]}"
    epub_name = _safe_output_name(pdf_path, output_name_value)
    job = ConversionJob(
      id=job_id,
      pdf_name=pdf_path.name,
      output_name=epub_name,
      status="queued",
      pdf_path=str(pdf_path),
      analysing_dir=str((paths.work_dir / "analysing" / job_id).resolve()),
      output_dir=str((paths.work_dir / "output" / job_id).resolve()),
      epub_path=str((paths.dist_dir / epub_name).resolve()),
      created_at=created_at,
      updated_at=created_at,
      logs=[f"[{_clock()}] 已加入队列：{pdf_path.name}"],
    )
    with self._lock:
      self._jobs[job.id] = job
      self._save_job(job)
      self._ensure_worker_locked()
      return self._snapshot(job)

  def pause(self, job_id: str) -> dict[str, Any]:
    with self._lock:
      job = self._require_job(job_id)
      if job.status == "queued":
        self._set_job_status(job, "paused", "已暂停")
        self._append_log_locked(job, "已暂停排队任务")
      elif job.status == "running":
        self._set_job_status(job, "pausing", "正在暂停")
        self._append_log_locked(job, "收到暂停请求，将在下一个检查点暂停")
      return self._snapshot(job)

  def resume(self, job_id: str) -> dict[str, Any]:
    with self._lock:
      job = self._require_job(job_id)
      if job.status not in ("paused", "canceled"):
        raise HTTPException(status_code=409, detail="只有已暂停或已取消的任务可以继续")
      self._queue_again_locked(job, "继续任务，复用已有 analysing_dir")
      self._ensure_worker_locked()
      return self._snapshot(job)

  def retry(self, job_id: str) -> dict[str, Any]:
    with self._lock:
      job = self._require_job(job_id)
      if job.status not in (*TERMINAL_STATUSES, "paused"):
        raise HTTPException(status_code=409, detail="当前任务状态不能重试")
      self._queue_again_locked(job, "任务已重新加入队列，复用已有 analysing_dir")
      self._ensure_worker_locked()
      return self._snapshot(job)

  def cancel(self, job_id: str) -> dict[str, Any]:
    with self._lock:
      job = self._require_job(job_id)
      if job.status in ("queued", "paused"):
        self._set_job_status(job, "canceled", "已取消")
        job.finished_at = _now()
        self._append_log_locked(job, "任务已取消")
      elif job.status == "running":
        self._set_job_status(job, "canceling", "正在取消")
        self._append_log_locked(job, "收到取消请求，将在下一个检查点停止")
      return self._snapshot(job)

  def delete(self, job_id: str, delete_files: bool = True) -> dict[str, Any]:
    with self._lock:
      job = self._require_job(job_id)
      if job.status in ("running", "pausing", "canceling"):
        raise HTTPException(status_code=409, detail="任务仍在运行，不能删除")
      deleted_paths: list[str] = []
      if delete_files:
        for path in self._job_paths(job):
          if not path.exists():
            continue
          self._assert_known_job_path(job, path)
          if path.is_dir():
            import shutil
            shutil.rmtree(path)
          else:
            path.unlink()
          deleted_paths.append(str(path))
      self._delete_job_file(job.id)
      del self._jobs[job.id]
      return {"deleted": deleted_paths, "jobs": self.list_jobs()}

  def delete_by_pdf(self, pdf_name: str, output_name_value: str | None) -> dict[str, Any]:
    with self._lock:
      target_pdf = Path(pdf_name).name
      output_candidate = None if output_name_value is None else Path(output_name_value).name
      matches = [
        job for job in self._jobs.values()
        if job.pdf_name == target_pdf and (output_candidate is None or Path(job.epub_path).name == output_candidate)
      ]
      if not matches:
        raise HTTPException(status_code=404, detail="任务不存在")
      return self.delete(matches[0].id)

  def start_worker(self) -> None:
    with self._lock:
      self._ensure_worker_locked()

  def _queue_again_locked(self, job: ConversionJob, message: str) -> None:
    job.status = "queued"
    job.step = "等待任务"
    job.progress_current = 0
    job.progress_total = None
    job.error = None
    job.output_file = None
    job.started_at = None
    job.finished_at = None
    self._append_log_locked(job, message)
    self._save_job(job)

  def _set_job_status(self, job: ConversionJob, status: str, step: str) -> None:
    job.status = status
    job.step = step
    job.updated_at = _now()
    self._save_job(job)

  def _append_log_locked(self, job: ConversionJob, message: str) -> None:
    job.logs.append(f"[{_clock()}] {message}")
    if len(job.logs) > 500:
      del job.logs[:len(job.logs) - 500]
    job.updated_at = _now()
    self._save_job(job)

  def _load_jobs(self) -> None:
    paths = ensure_runtime_dirs()
    jobs_dir = paths.work_dir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    with self._lock:
      if self._jobs_dir == jobs_dir and self._jobs:
        return
      self._jobs_dir = jobs_dir
      self._jobs = {}
      for path in sorted(jobs_dir.glob("*.json")):
        try:
          data = json.loads(path.read_text(encoding="utf-8"))
          job = ConversionJob(**data)
          if job.status in RESTART_PAUSED_STATUSES:
            job.status = "paused"
            job.step = "服务重启后已暂停"
            job.updated_at = _now()
            job.logs.append(f"[{_clock()}] 服务重启后任务已暂停，可继续或重试")
            self._save_job(job)
          self._jobs[job.id] = job
        except (OSError, TypeError, json.JSONDecodeError):
          continue

  def _save_job(self, job: ConversionJob) -> None:
    if self._jobs_dir is None:
      self._jobs_dir = runtime_paths().work_dir / "jobs"
      self._jobs_dir.mkdir(parents=True, exist_ok=True)
    job.updated_at = job.updated_at or _now()
    path = self._jobs_dir / f"{job.id}.json"
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(asdict(job), ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)

  def _delete_job_file(self, job_id: str) -> None:
    if self._jobs_dir is None:
      return
    path = self._jobs_dir / f"{job_id}.json"
    if path.exists():
      path.unlink()

  def _ensure_worker_locked(self) -> None:
    if self._worker is not None and self._worker.is_alive():
      return
    if not any(job.status == "queued" for job in self._jobs.values()):
      return
    self._worker = threading.Thread(target=self._worker_loop, daemon=True)
    self._worker.start()

  def _worker_loop(self) -> None:
    while True:
      with self._lock:
        queued = sorted(
          (job for job in self._jobs.values() if job.status == "queued"),
          key=lambda job: job.created_at,
        )
        if not queued:
          return
        job = queued[0]
        job.status = "running"
        job.step = "准备开始"
        job.progress_current = 0
        job.progress_total = None
        job.error = None
        job.output_file = None
        job.started_at = _now()
        job.finished_at = None
        self._append_log_locked(job, "任务开始运行")
        self._save_job(job)
      self._run_job(job.id)

  def _run_job(self, job_id: str) -> None:
    try:
      with self._lock:
        job = self._require_job(job_id)
        paths = RuntimePaths(
          input_dir=Path(job.pdf_path).parent,
          work_dir=Path(job.analysing_dir).parents[1],
          dist_dir=Path(job.epub_path).parent,
          model_dir=runtime_paths().model_dir,
        )
        pdf_path = Path(job.pdf_path)
        output_name_value = job.output_name
        analysing_dir = Path(job.analysing_dir)
        output_dir = Path(job.output_dir)
        epub_path = Path(job.epub_path)

      result = convert_pdf_to_epub(
        pdf_path=pdf_path,
        output_filename=output_name_value,
        paths=paths,
        analysing_dir=analysing_dir,
        output_dir=output_dir,
        epub_path=epub_path,
        log=lambda message: self._log_and_guard(job_id, message),
        report_step=lambda step: self._set_step_and_guard(job_id, step),
        report_progress=lambda current, total: self._set_progress_and_guard(job_id, current, total),
      )
      self._complete_job(job_id, result)
    except JobPaused:
      with self._lock:
        job = self._require_job(job_id)
        self._set_job_status(job, "paused", "已暂停")
        job.finished_at = _now()
        self._append_log_locked(job, "任务已暂停，可继续复用 analysing_dir")
    except JobCancelled:
      with self._lock:
        job = self._require_job(job_id)
        self._set_job_status(job, "canceled", "已取消")
        job.finished_at = _now()
        self._append_log_locked(job, "任务已取消，保留中间文件用于重试")
    except Exception as err:  # pylint: disable=broad-exception-caught
      with self._lock:
        job = self._require_job(job_id)
        job.status = "failed"
        job.error = str(err)
        job.finished_at = _now()
        job.step = "任务失败"
        self._append_log_locked(job, traceback.format_exc())
        self._save_job(job)

  def _complete_job(self, job_id: str, result: ConversionResult) -> None:
    with self._lock:
      job = self._require_job(job_id)
      job.status = "done"
      job.step = "已完成"
      job.progress_current = job.progress_total or job.progress_current
      job.output_file = result.epub_path.name
      job.finished_at = _now()
      self._append_log_locked(job, f"任务完成：{result.epub_path}")
      self._save_job(job)

  def _log_and_guard(self, job_id: str, message: str) -> None:
    with self._lock:
      job = self._require_job(job_id)
      self._append_log_locked(job, message)
    self._guard(job_id)

  def _set_step_and_guard(self, job_id: str, step: str) -> None:
    with self._lock:
      job = self._require_job(job_id)
      job.step = step
      job.progress_current = 0
      job.progress_total = None
      job.updated_at = _now()
      self._save_job(job)
    self._guard(job_id)

  def _set_progress_and_guard(self, job_id: str, current: int, total: int | None) -> None:
    with self._lock:
      job = self._require_job(job_id)
      job.progress_current = current
      job.progress_total = total
      job.updated_at = _now()
      self._save_job(job)
    self._guard(job_id)

  def _guard(self, job_id: str) -> None:
    with self._lock:
      status = self._require_job(job_id).status
    if status == "pausing":
      raise JobPaused()
    if status == "canceling":
      raise JobCancelled()

  def _require_job(self, job_id: str) -> ConversionJob:
    job = self._jobs.get(job_id)
    if job is None:
      raise HTTPException(status_code=404, detail="任务不存在")
    return job

  def _snapshot(self, job: ConversionJob) -> dict[str, Any]:
    data = asdict(job)
    data["download_url"] = f"/api/download/{Path(job.output_file).name}" if job.output_file else None
    data["can_pause"] = job.status in ("queued", "running")
    data["can_resume"] = job.status in ("paused", "canceled")
    data["can_cancel"] = job.status in ("queued", "running", "paused")
    data["can_retry"] = job.status in (*TERMINAL_STATUSES, "paused")
    data["can_delete"] = job.status not in ("running", "pausing", "canceling")
    return data

  def _input_pdf_path(self, paths, pdf_name: str) -> Path:
    path = (paths.input_dir / Path(pdf_name).name).resolve()
    if paths.input_dir.resolve() not in path.parents:
      raise HTTPException(status_code=400, detail="PDF 路径不合法")
    if not path.exists():
      raise HTTPException(status_code=404, detail="PDF 文件不存在")
    return path

  def _job_paths(self, job: ConversionJob) -> list[Path]:
    return [
      Path(job.pdf_path),
      Path(job.analysing_dir),
      Path(job.output_dir),
      Path(job.epub_path),
    ]

  def _assert_known_job_path(self, job: ConversionJob, path: Path) -> None:
    resolved = path.resolve()
    allowed = [candidate.resolve() for candidate in self._job_paths(job)]
    if resolved not in allowed:
      raise HTTPException(status_code=400, detail="任务路径不合法")
