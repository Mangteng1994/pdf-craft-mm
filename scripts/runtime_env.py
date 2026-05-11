from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_DIR = ROOT_DIR / ".venvs"


@dataclass(frozen=True)
class RuntimeFlavor:
  name: str
  package_extra: str
  device: str
  env_dir: Path

  @property
  def scripts_dir(self) -> Path:
    return self.env_dir / ("Scripts" if os.name == "nt" else "bin")

  @property
  def python_path(self) -> Path:
    exe_name = "python.exe" if os.name == "nt" else "python"
    return self.scripts_dir / exe_name


FLAVORS = {
  "cpu": RuntimeFlavor(
    name="cpu",
    package_extra="cpu",
    device="cpu",
    env_dir=ENV_DIR / "cpu",
  ),
  "gpu": RuntimeFlavor(
    name="gpu",
    package_extra="cuda",
    device="cuda",
    env_dir=ENV_DIR / "gpu",
  ),
}


def _parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(
    description="Create, validate, and run isolated CPU/GPU runtime environments for PDF Craft.",
  )
  subparsers = parser.add_subparsers(dest="command", required=True)

  create_parser = subparsers.add_parser("create", help="Create or update a CPU/GPU runtime environment.")
  create_parser.add_argument("flavor", choices=sorted(FLAVORS.keys()))
  create_parser.add_argument(
    "--python",
    default=sys.executable,
    help="Python interpreter used to create the virtual environment. Defaults to the current interpreter.",
  )

  check_parser = subparsers.add_parser("check", help="Print onnxruntime package/provider information.")
  check_parser.add_argument("flavor", choices=sorted(FLAVORS.keys()))

  path_parser = subparsers.add_parser("python-path", help="Print the Python executable path for a runtime environment.")
  path_parser.add_argument("flavor", choices=sorted(FLAVORS.keys()))

  run_web_parser = subparsers.add_parser("run-web", help="Start the FastAPI web app with a selected runtime environment.")
  run_web_parser.add_argument("flavor", choices=sorted(FLAVORS.keys()))
  run_web_parser.add_argument("--host", default="127.0.0.1")
  run_web_parser.add_argument("--port", default="8000")
  run_web_parser.add_argument("--reload", action="store_true")

  return parser.parse_args()


def _run(command: list[str], *, env: dict[str, str] | None = None) -> None:
  subprocess.run(command, cwd=ROOT_DIR, env=env, check=True)


def _create_env(flavor: RuntimeFlavor, python_executable: str) -> None:
  flavor.env_dir.parent.mkdir(parents=True, exist_ok=True)
  _run([python_executable, "-m", "venv", str(flavor.env_dir)])
  _run([str(flavor.python_path), "-m", "pip", "install", "--upgrade", "pip"])
  _run([str(flavor.python_path), "-m", "pip", "install", "-e", f".[{flavor.package_extra}]"])


def _print_python_path(flavor: RuntimeFlavor) -> None:
  print(flavor.python_path)


def _check_env(flavor: RuntimeFlavor) -> None:
  if not flavor.python_path.exists():
    raise SystemExit(
      f"Runtime environment not found: {flavor.env_dir}. Run `python scripts/runtime_env.py create {flavor.name}` first.",
    )

  script = """
from importlib.metadata import PackageNotFoundError, version
import json

def package_version(name):
  try:
    return version(name)
  except PackageNotFoundError:
    return None

import onnxruntime as ort
payload = {
  "onnxruntime": package_version("onnxruntime"),
  "onnxruntime-gpu": package_version("onnxruntime-gpu"),
  "available_providers": ort.get_available_providers(),
  "device": %r,
}
print(json.dumps(payload, ensure_ascii=False, indent=2))
""" % flavor.device
  _run([str(flavor.python_path), "-c", script])


def _run_web(flavor: RuntimeFlavor, host: str, port: str, reload_enabled: bool) -> None:
  if not flavor.python_path.exists():
    raise SystemExit(
      f"Runtime environment not found: {flavor.env_dir}. Run `python scripts/runtime_env.py create {flavor.name}` first.",
    )

  env = os.environ.copy()
  env["PDF_CRAFT_DEVICE"] = flavor.device
  command = [
    str(flavor.python_path),
    "-m",
    "uvicorn",
    "web_app:app",
    "--host",
    host,
    "--port",
    port,
  ]
  if reload_enabled:
    command.append("--reload")
  subprocess.run(command, cwd=ROOT_DIR, env=env, check=True)


def main() -> None:
  args = _parse_args()
  flavor = FLAVORS[args.flavor]

  if args.command == "create":
    _create_env(flavor, args.python)
    return
  if args.command == "check":
    _check_env(flavor)
    return
  if args.command == "python-path":
    _print_python_path(flavor)
    return
  if args.command == "run-web":
    _run_web(flavor, args.host, args.port, args.reload)
    return
  raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
  main()
