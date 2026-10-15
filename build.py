"""Build GoXLR_Discord_Sync.exe and GoXLR_Setup.exe with PyInstaller.

Usage: python build.py [app|setup|all]   (default: all)
Exits with a non-zero code on failure so scripts and CI can stop.
"""

import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
sys.path.insert(0, str(ROOT))

from goxlr_discord_sync import __version__  # noqa: E402

APP_NAME = "GoXLR_Discord_Sync"
SETUP_NAME = "GoXLR_Setup"
EXE = ".exe" if os.name == "nt" else ""


def version_file(name: str, description: str) -> Path:
    parts = [int(p) for p in __version__.split(".")] + [0] * 4
    tuple_version = tuple(parts[:4])
    content = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={tuple_version}, prodvers={tuple_version}),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'powange'),
      StringStruct('FileDescription', '{description}'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', '{name}'),
      StringStruct('OriginalFilename', '{name}.exe'),
      StringStruct('ProductName', 'GoXLR Discord Sync'),
      StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    path = BUILD / f"{name}_version.txt"
    path.write_text(content, encoding="utf-8")
    return path


def icon_file() -> Path:
    from goxlr_discord_sync.icons import save_ico

    path = BUILD / "app.ico"
    save_ico(path)
    return path


def pyinstaller(args: list[str]) -> None:
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
               "--distpath", str(DIST), "--workpath", str(BUILD / "work"), "--specpath", str(BUILD), *args]
    print(">", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def stop_running(exe_name: str) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/IM", exe_name], capture_output=True, check=False)


def windows_only_args(name: str, description: str) -> list[str]:
    if os.name != "nt":
        return []
    return ["--icon", str(icon_file()), "--version-file", str(version_file(name, description))]


def build_app() -> Path:
    stop_running(f"{APP_NAME}.exe")
    pyinstaller([
        "--name", APP_NAME,
        *windows_only_args(APP_NAME, "GoXLR Discord Sync"),
        str(ROOT / "goxlr_discord_sync.pyw"),
    ])
    return DIST / f"{APP_NAME}{EXE}"


def build_setup() -> Path:
    if os.name != "nt":
        sys.exit("The setup wizard executable is Windows only. On Linux run: python setup_gui.py")
    app_exe = DIST / f"{APP_NAME}.exe"
    if not app_exe.exists():
        sys.exit(f"{app_exe} not found: build the application first (python build.py app)")
    stop_running(f"{SETUP_NAME}.exe")
    pyinstaller([
        "--name", SETUP_NAME,
        *windows_only_args(SETUP_NAME, "GoXLR Discord Sync Setup"),
        "--add-data", f"{app_exe}{os.pathsep}.",
        str(ROOT / "setup_gui.py"),
    ])
    return DIST / f"{SETUP_NAME}.exe"


def write_checksums(files: list[Path]) -> None:
    lines = [f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}" for f in files if f.exists()]
    (DIST / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    if target not in ("app", "setup", "all"):
        print(__doc__)
        return 2
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is missing: pip install -r requirements-build.txt")
        return 1
    BUILD.mkdir(exist_ok=True)
    print(f"Building GoXLR Discord Sync {__version__} ({target})")
    try:
        built = []
        if target in ("app", "all"):
            built.append(build_app())
        if target in ("setup", "all"):
            built.append(build_setup())
    except subprocess.CalledProcessError as e:
        print(f"Build failed (exit code {e.returncode})")
        return 1
    missing = [f for f in built if not f.exists()]
    if missing:
        print("Build failed, missing:", ", ".join(map(str, missing)))
        return 1
    write_checksums(built)
    for f in built:
        print(f"Built {f} ({f.stat().st_size // 1024} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
