import base64
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("build_script", ROOT / "build.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def test_signing_is_skipped_without_certificate(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("SIGN_PFX_BASE64", raising=False)
    monkeypatch.setattr(build.subprocess, "run", lambda *a, **k: pytest.fail("signtool must not run"))
    build.sign(tmp_path / "app.exe")
    assert "Not signed" in capsys.readouterr().out


def test_signing_uses_signtool_and_removes_the_key(monkeypatch, tmp_path):
    monkeypatch.setattr(build, "BUILD", tmp_path)
    monkeypatch.setenv("SIGN_PFX_BASE64", base64.b64encode(b"fake pfx").decode())
    monkeypatch.setenv("SIGN_PFX_PASSWORD", "secret")
    monkeypatch.setattr(build, "find_signtool", lambda: "signtool.exe")
    calls = []

    def run(command, check):
        assert (tmp_path / "signing.pfx").read_bytes() == b"fake pfx"
        calls.append(command)

    monkeypatch.setattr(build.subprocess, "run", run)
    build.sign(tmp_path / "GoXLR_Setup.exe")
    command = calls[0]
    assert command[:2] == ["signtool.exe", "sign"]
    assert command[2:4] == ["/fd", "SHA256"] and "/tr" in command and command[-1].endswith("GoXLR_Setup.exe")
    assert not (tmp_path / "signing.pfx").exists()


def test_missing_signtool_fails_the_build(monkeypatch, tmp_path):
    monkeypatch.setattr(build, "BUILD", tmp_path)
    monkeypatch.setenv("SIGN_PFX_BASE64", base64.b64encode(b"x").decode())
    monkeypatch.setattr(build, "find_signtool", lambda: None)
    with pytest.raises(SystemExit):
        build.sign(tmp_path / "app.exe")


def test_checksums_target(monkeypatch, tmp_path):
    monkeypatch.setattr(build, "DIST", tmp_path)
    monkeypatch.setattr(build, "EXE", ".exe")
    (tmp_path / "GoXLR_Setup.exe").write_bytes(b"setup")
    (tmp_path / "GoXLR_Discord_Sync.exe").write_bytes(b"app")
    monkeypatch.setattr(build.sys, "argv", ["build.py", "checksums"])
    assert build.main() == 0
    lines = (tmp_path / "SHA256SUMS.txt").read_text().splitlines()
    assert [line.split()[1] for line in lines] == ["GoXLR_Discord_Sync.exe", "GoXLR_Setup.exe"]
