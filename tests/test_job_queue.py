import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from conversion_service import ConversionResult
from job_queue import JobQueue


class TestJobQueue(unittest.TestCase):
  def setUp(self):
    self._original_env = dict(os.environ)
    self._temp_dir = tempfile.TemporaryDirectory()
    self.root = Path(self._temp_dir.name)
    self.input_dir = self.root / "inputs"
    self.work_dir = self.root / "work"
    self.dist_dir = self.root / "dist"
    self.model_dir = self.root / "models"
    self.input_dir.mkdir()
    self.work_dir.mkdir()
    self.dist_dir.mkdir()
    self.model_dir.mkdir()
    self.pdf_path = self.input_dir / "book.pdf"
    self.pdf_path.write_bytes(b"%PDF-1.4\n")
    os.environ.update({
      "PDF_CRAFT_INPUT_DIR": str(self.input_dir),
      "PDF_CRAFT_WORK_DIR": str(self.work_dir),
      "PDF_CRAFT_DIST_DIR": str(self.dist_dir),
      "PDF_CRAFT_MODEL_DIR": str(self.model_dir),
    })

  def tearDown(self):
    os.environ.clear()
    os.environ.update(self._original_env)
    self._temp_dir.cleanup()

  def _wait_for_status(self, queue: JobQueue, job_id: str, status: str) -> dict:
    deadline = time.time() + 5
    while time.time() < deadline:
      job = queue.get_job(job_id)
      if job["status"] == status:
        return job
      time.sleep(0.05)
    self.fail(f"Job {job_id} did not reach {status}: {queue.get_job(job_id)}")

  def test_queued_job_can_be_paused_and_resumed_with_status_file(self):
    queue = JobQueue()
    with patch.object(queue, "_ensure_worker_locked"):
      job = queue.enqueue("book.pdf", None)
      paused = queue.pause(job["id"])
      resumed = queue.resume(job["id"])

    self.assertEqual(paused["status"], "paused")
    self.assertEqual(resumed["status"], "queued")
    self.assertTrue((self.work_dir / "jobs" / f"{job['id']}.json").exists())
    self.assertIn(job["id"], resumed["analysing_dir"])

  def test_failed_job_retries_with_same_analysing_dir(self):
    queue = JobQueue()
    analysing_dirs = []

    def fake_convert(**kwargs):
      analysing_dirs.append(str(kwargs["analysing_dir"]))
      if len(analysing_dirs) == 1:
        raise RuntimeError("boom")
      epub_path = Path(kwargs["epub_path"])
      epub_path.parent.mkdir(parents=True, exist_ok=True)
      epub_path.write_bytes(b"epub")
      return ConversionResult(
        pdf_path=Path(kwargs["pdf_path"]),
        analysing_dir=Path(kwargs["analysing_dir"]),
        output_dir=Path(kwargs["output_dir"]),
        epub_path=epub_path,
      )

    with patch("job_queue.convert_pdf_to_epub", side_effect=fake_convert):
      job = queue.enqueue("book.pdf", "book.epub")
      failed = self._wait_for_status(queue, job["id"], "failed")
      retried = queue.retry(job["id"])
      done = self._wait_for_status(queue, retried["id"], "done")

    self.assertEqual(failed["error"], "boom")
    self.assertEqual(done["output_file"], "book.epub")
    self.assertEqual(analysing_dirs[0], analysing_dirs[1])

  def test_delete_removes_job_record_and_related_files(self):
    queue = JobQueue()
    with patch.object(queue, "_ensure_worker_locked"):
      job = queue.enqueue("book.pdf", "book.epub")

    Path(job["analysing_dir"]).mkdir(parents=True)
    Path(job["output_dir"]).mkdir(parents=True)
    Path(job["epub_path"]).write_bytes(b"epub")

    result = queue.delete(job["id"])

    self.assertEqual(result["jobs"], [])
    self.assertFalse(self.pdf_path.exists())
    self.assertFalse(Path(job["analysing_dir"]).exists())
    self.assertFalse(Path(job["output_dir"]).exists())
    self.assertFalse(Path(job["epub_path"]).exists())
    self.assertFalse((self.work_dir / "jobs" / f"{job['id']}.json").exists())
