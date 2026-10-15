"""Build GoXLR_Discord_Sync.exe and GoXLR_Setup.exe with PyInstaller.

Usage: python build.py [app|setup|all|checksums]   (default: all)
Exits with a non-zero code on failure so scripts and CI can stop.

Code signing with a certificate file (optional): set SIGN_PFX_BASE64 (the
.pfx file, base64) and SIGN_PFX_PASSWORD; each executable is then signed
with signtool right after it is built, so the setup bundles a signed app.
SignPath signing happens in the CI workflow instead (see README).
"""

import base64
import glob
import hashlib
import os
import shutil
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
TIMESTAMP_URL = os.environ.get("SIGN_TIMESTAMP_URL", "http://timestamp.digicert.com")


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


def find_signtool() -> str | None:
    found = shutil.which("signtool")
    if found:
        return found
    kits = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    candidates = glob.glob(os.path.join(kits, "Windows Kits", "10", "bin", "10.*", "x64", "signtool.exe"))
    return max(candidates, key=lambda p: [int(x) for x in Path(p).parts[-3].split(".")]) if candidates else None


def signtool_command(signtool: str, pfx: Path, password: str, target: Path) -> list[str]:
    return [
        signtool, "sign", "/fd", "SHA256", "/tr", TIMESTAMP_URL, "/td", "SHA256",
        "/f", str(pfx), "/p", password,
        "/d", "GoXLR Discord Sync", "/du", "https://github.com/powange/goxlr-discord-sync",
        str(target),
    ]


def sign(target: Path) -> None:
    """Sign target with the certificate given in SIGN_PFX_BASE64, when there is one."""
    encoded = os.environ.get("SIGN_PFX_BASE64", "").strip()
    if not encoded:
        print(f"Not signed (no SIGN_PFX_BASE64): {target.name}")
        return
    signtool = find_signtool()
    if signtool is None:
        sys.exit("SIGN_PFX_BASE64 is set but signtool.exe was not found (install the Windows SDK)")
    pfx = BUILD / "signing.pfx"
    pfx.write_bytes(base64.b64decode(encoded))
    try:
        command = signtool_command(signtool, pfx, os.environ.get("SIGN_PFX_PASSWORD", ""), target)
        print("> signtool sign", target.name, flush=True)
        subprocess.run(command, check=True)
    finally:
        pfx.unlink(missing_ok=True)  # never leave the private key on disk


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
    app = DIST / f"{APP_NAME}{EXE}"
    if app.exists() and os.name == "nt":
        sign(app)  # before the setup bundles it
    return app


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
    setup = DIST / f"{SETUP_NAME}.exe"
    if setup.exists():
        sign(setup)
    return setup


def write_checksums(files: list[Path]) -> None:
    lines = [f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}" for f in files if f.exists()]
    (DIST / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    if target not in ("app", "setup", "all", "checksums"):
        print(__doc__)
        return 2
    if target == "checksums":  # after an external signing step changed the files
        files = sorted(DIST.glob(f"*{EXE}" if EXE else f"{APP_NAME}*"))
        write_checksums(files)
        print((DIST / "SHA256SUMS.txt").read_text(encoding="utf-8"), end="")
        return 0
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
