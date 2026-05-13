from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


FLAVORS = {
  "cpu": "cpu",
  "gpu": "cuda",
}


def _parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(description="Start the PDF Craft local web app.")
  parser.add_argument("--flavor", choices=sorted(FLAVORS.keys()), default=os.getenv("PDF_CRAFT_LAUNCHER_FLAVOR"))
  parser.add_argument("--host", default=os.getenv("PDF_CRAFT_HOST", "127.0.0.1"))
  parser.add_argument("--port", default=os.getenv("PDF_CRAFT_PORT", "8000"))
  parser.add_argument("--no-browser", action="store_true", help="Do not open the browser automatically.")
  return parser.parse_args()


def _exe_dir() -> Path:
  if getattr(sys, "frozen", False):
    return Path(sys.executable).resolve().parent
  return Path(__file__).resolve().parent


def _find_root_dir() -> Path:
  env_root = os.getenv("PDF_CRAFT_ROOT", "").strip()
  candidates = []
  if env_root:
    candidates.append(Path(env_root))

  base = _exe_dir()
  candidates.extend([base, base.parent, Path.cwd()])
  candidates.extend(base.parents)

  for candidate in candidates:
    root = candidate.resolve()
    if (root / "web_app.py").exists() and (root / "scripts" / "runtime_env.py").exists():
      return root

  raise SystemExit(
    "Cannot find PDF Craft project files. Put this launcher in the project root, "
    "or set PDF_CRAFT_ROOT to the project directory.",
  )


def _runtime_python(root_dir: Path, flavor: str) -> Path:
  return root_dir / ".venvs" / flavor / "Scripts" / "python.exe"


def _read_env_device(root_dir: Path) -> str:
  env_path = root_dir / ".env"
  if not env_path.exists():
    return "cpu"

  for raw_line in env_path.read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
      continue
    key, value = line.split("=", 1)
    key = key.removeprefix("export ").strip()
    if key == "PDF_CRAFT_DEVICE":
      return value.strip().strip('"').strip("'").lower()
  return "cpu"


def _select_flavor(root_dir: Path, requested_flavor: str | None) -> str:
  if requested_flavor:
    return requested_flavor
  return "gpu" if _read_env_device(root_dir) == "cuda" else "cpu"


def _find_bootstrap_python() -> list[str]:
  candidates = [
    ["py", "-3.12"],
    ["py", "-3.11"],
    ["py", "-3.10"],
    ["python"],
  ]
  for command in candidates:
    try:
      result = subprocess.run(
        [*command, "-c", "import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] < (3, 13) else 1)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
      )
    except OSError:
      continue
    if result.returncode == 0:
      return command
  raise SystemExit("Python 3.10-3.12 is required to create the runtime environment.")


def _run_command(command: list[str], root_dir: Path) -> None:
  print("Running:", " ".join(command), flush=True)
  subprocess.run(command, cwd=root_dir, check=True)


def _create_runtime_if_missing(root_dir: Path, flavor: str) -> None:
  python_path = _runtime_python(root_dir, flavor)
  if python_path.exists():
    return

  print(f"Runtime environment .venvs/{flavor} was not found.", flush=True)
  bootstrap = _find_bootstrap_python()
  _run_command([*bootstrap, str(root_dir / "scripts" / "runtime_env.py"), "create", flavor], root_dir)


def _port_is_open(host: str, port: int) -> bool:
  with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.settimeout(0.5)
    return sock.connect_ex((host, port)) == 0


def _wait_for_server(url: str, timeout_seconds: int = 60) -> bool:
  deadline = time.monotonic() + timeout_seconds
  while time.monotonic() < deadline:
    try:
      with urllib.request.urlopen(url, timeout=1):
        return True
    except (urllib.error.URLError, TimeoutError):
      time.sleep(0.5)
  return False


def _start_web(root_dir: Path, flavor: str, host: str, port: str) -> subprocess.Popen[bytes]:
  env = os.environ.copy()
  env["PDF_CRAFT_DEVICE"] = FLAVORS[flavor]
  command = [
    str(_runtime_python(root_dir, flavor)),
    "-m",
    "uvicorn",
    "web_app:app",
    "--host",
    host,
    "--port",
    port,
  ]
  print("Starting PDF Craft Web:", " ".join(command), flush=True)
  return subprocess.Popen(command, cwd=root_dir, env=env)


def main() -> int:
  args = _parse_args()
  root_dir = _find_root_dir()
  flavor = _select_flavor(root_dir, args.flavor)
  port = int(args.port)
  url = f"http://{args.host}:{port}"

  print(f"PDF Craft root: {root_dir}", flush=True)
  print(f"Runtime flavor: {flavor}", flush=True)
  _create_runtime_if_missing(root_dir, flavor)

  if _port_is_open(args.host, port):
    print(f"{url} is already running.", flush=True)
    if not args.no_browser:
      webbrowser.open(url)
    return 0

  process = _start_web(root_dir, flavor, args.host, args.port)
  if _wait_for_server(url) and not args.no_browser:
    webbrowser.open(url)
    print(f"Opened {url}", flush=True)
  else:
    print(f"Server is starting. Open {url} manually if the browser does not appear.", flush=True)

  try:
    return process.wait()
  except KeyboardInterrupt:
    process.terminate()
    return 130


if __name__ == "__main__":
  raise SystemExit(main())
