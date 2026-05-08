from __future__ import annotations

import argparse

from conversion_service import convert_pdf_to_epub, ensure_runtime_dirs, find_default_pdf, resolve_path


def main() -> None:
  parser = argparse.ArgumentParser(description="Convert a scanned PDF book to EPUB with pdf-craft.")
  parser.add_argument("pdf", nargs="?", help="Source PDF path. Defaults to the first PDF in inputs/.")
  parser.add_argument("-o", "--output", help="Output EPUB file name, for example my-book.epub.")
  args = parser.parse_args()

  paths = ensure_runtime_dirs()
  pdf_path = resolve_path(args.pdf) if args.pdf else find_default_pdf(paths.input_dir)
  convert_pdf_to_epub(pdf_path, output_filename=args.output, log=print)


if __name__ == "__main__":
  main()
