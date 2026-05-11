from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from llm_config import ROOT_DIR, _load_env_file, create_llm


LogCallback = Callable[[str], None]
StepCallback = Callable[[str], None]
ProgressCallback = Callable[[int, int | None], None]


@dataclass(frozen=True)
class RuntimePaths:
  input_dir: Path
  work_dir: Path
  dist_dir: Path
  model_dir: Path


@dataclass(frozen=True)
class ConversionResult:
  pdf_path: Path
  analysing_dir: Path
  output_dir: Path
  epub_path: Path


STEP_LABELS = {
  "OCR": "OCR 识别",
  "EXTRACT_SEQUENCE": "提取文本顺序",
  "VERIFY_TEXT_PARAGRAPH": "校验正文段落",
  "VERIFY_FOOTNOTE_PARAGRAPH": "校验脚注段落",
  "CORRECT_TEXT": "正文勘误",
  "CORRECT_FOOTNOTE": "脚注勘误",
  "EXTRACT_META": "提取书籍信息",
  "COLLECT_CONTENTS": "收集目录",
  "ANALYSE_CONTENTS": "分析目录",
  "MAPPING_CONTENTS": "映射章节",
  "GENERATE_FOOTNOTES": "生成脚注",
  "OUTPUT": "写出中间结果",
}


def env(name: str, default: str) -> str:
  return os.environ.get(name, default)


def env_bool(name: str, default: bool) -> bool:
  value = os.environ.get(name)
  if value is None or value == "":
    return default
  return value.lower() in ("1", "true", "yes", "on")


def env_int(name: str, default: int) -> int:
  value = os.environ.get(name)
  return default if value in (None, "") else int(value)


def resolve_path(value: str | Path) -> Path:
  path = Path(value)
  return path if path.is_absolute() else ROOT_DIR / path


def runtime_paths() -> RuntimePaths:
  _load_env_file()
  return RuntimePaths(
    input_dir=resolve_path(env("PDF_CRAFT_INPUT_DIR", "inputs")),
    work_dir=resolve_path(env("PDF_CRAFT_WORK_DIR", "work")),
    dist_dir=resolve_path(env("PDF_CRAFT_DIST_DIR", "dist")),
    model_dir=resolve_path(env("PDF_CRAFT_MODEL_DIR", "models")),
  )


def ensure_runtime_dirs(paths: RuntimePaths | None = None) -> RuntimePaths:
  paths = runtime_paths() if paths is None else paths
  for path in (paths.input_dir, paths.work_dir, paths.dist_dir, paths.model_dir):
    path.mkdir(parents=True, exist_ok=True)
  return paths


def find_default_pdf(input_dir: Path) -> Path:
  pdfs = sorted(input_dir.glob("*.pdf"))
  if not pdfs:
    raise FileNotFoundError(f"No PDF found in {input_dir}. Put your source PDF there or pass it as an argument.")
  return pdfs[0]


def output_name(pdf_path: Path, output_name: str | None) -> str:
  if output_name:
    return output_name if output_name.lower().endswith(".epub") else f"{output_name}.epub"
  return f"{pdf_path.stem}.epub"


def table_format():
  from pdf_craft import ExtractedTableFormat

  value = env("PDF_CRAFT_EXTRACT_TABLE_FORMAT", "disable").lower()
  mapping = {
    "disable": ExtractedTableFormat.DISABLE,
    "latex": ExtractedTableFormat.LATEX,
    "markdown": ExtractedTableFormat.MARKDOWN,
    "html": ExtractedTableFormat.HTML,
  }
  if value in ("", "auto", "none"):
    return None
  if value not in mapping:
    raise ValueError(f"Unsupported PDF_CRAFT_EXTRACT_TABLE_FORMAT: {value}")
  return mapping[value]


def correction_mode():
  from pdf_craft import CorrectionMode

  value = env("PDF_CRAFT_CORRECTION_MODE", "no").lower()
  mapping = {
    "no": CorrectionMode.NO,
    "off": CorrectionMode.NO,
    "false": CorrectionMode.NO,
    "once": CorrectionMode.ONCE,
    "detailed": CorrectionMode.DETAILED,
  }
  if value not in mapping:
    raise ValueError(f"Unsupported PDF_CRAFT_CORRECTION_MODE: {value}")
  return mapping[value]


def convert_pdf_to_epub(
    pdf_path: str | Path,
    output_filename: str | None = None,
    paths: RuntimePaths | None = None,
    analysing_dir: str | Path | None = None,
    output_dir: str | Path | None = None,
    epub_path: str | Path | None = None,
    log: LogCallback | None = None,
    report_step: StepCallback | None = None,
    report_progress: ProgressCallback | None = None,
  ) -> ConversionResult:
  _load_env_file()
  paths = ensure_runtime_dirs(paths)
  pdf_path = resolve_path(pdf_path)
  if not pdf_path.exists():
    raise FileNotFoundError(f"Source PDF not found: {pdf_path}")

  output_dir = Path(output_dir) if output_dir is not None else paths.work_dir / "output" / pdf_path.stem
  analysing_dir = Path(analysing_dir) if analysing_dir is not None else paths.work_dir / "analysing" / pdf_path.stem
  epub_path = Path(epub_path) if epub_path is not None else paths.dist_dir / output_name(pdf_path, output_filename)
  window_tokens_raw = env("PDF_CRAFT_WINDOW_TOKENS", "")
  window_tokens = None if window_tokens_raw == "" else int(window_tokens_raw)

  def emit(message: str) -> None:
    if log is not None:
      log(message)

  def step_callback(step) -> None:
    step_name = getattr(step, "name", str(step))
    label = STEP_LABELS.get(step_name, step_name)
    if report_step is not None:
      report_step(label)
    emit(f"步骤：{label}")

  emit(f"源 PDF：{pdf_path}")
  emit(f"中间分析目录：{analysing_dir}")
  emit(f"结构化输出目录：{output_dir}")
  emit(f"目标 EPUB：{epub_path}")

  from pdf_craft import analyse, create_pdf_page_extractor, generate_epub_file

  llm = create_llm()
  extractor = create_pdf_page_extractor(
    device=env("PDF_CRAFT_DEVICE", "cpu"),
    model_dir_path=paths.model_dir,
    extract_formula=env_bool("PDF_CRAFT_EXTRACT_FORMULA", False),
    extract_table_format=table_format(),
  )

  analyse(
    llm=llm,
    pdf_page_extractor=extractor,
    pdf_path=pdf_path,
    analysing_dir_path=analysing_dir,
    output_dir_path=output_dir,
    report_step=step_callback,
    report_progress=report_progress,
    correction_mode=correction_mode(),
    window_tokens=window_tokens,
    threads_count=env_int("PDF_CRAFT_THREADS_COUNT", 1),
  )
  emit("正在生成 EPUB 文件")
  generate_epub_file(
    from_dir_path=output_dir,
    epub_file_path=epub_path,
  )
  emit(f"完成：{epub_path}")

  return ConversionResult(
    pdf_path=pdf_path,
    analysing_dir=analysing_dir,
    output_dir=output_dir,
    epub_path=epub_path,
  )
