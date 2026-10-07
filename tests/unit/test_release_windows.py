"""Windows-Build vorbereiten: Workflow, PowerShell-Skript, Test-/Release-Trennung, MSI-Prüfung.

Diese Tests laufen unter Linux und belegen NICHT, dass der Windows-Build funktioniert. Sie halten
fest, was ohne Windows prüfbar ist: Aufbau der Dateien, Trennung Testbuild/Release, Prüflogik.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "windows-release.yml"
SCRIPT = ROOT / "tools" / "release" / "build-windows.ps1"


def _build() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "release_build_windows", ROOT / "tools" / "release" / "build.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build = _build()


# ---------- Workflow ----------


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _code(text: str, comment: str = "#") -> str:
    """Nur ausführbarer Inhalt: ohne Kommentarzeilen und PowerShell-Hilfeblock."""
    text = re.sub(r"<#.*?#>", "", text, flags=re.S)
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith(comment))


def test_workflow_never_hides_failures() -> None:
    code = _code(_workflow())
    assert re.search(r"^\s*continue-on-error\s*:", code, re.M) is None
    assert "|| true" not in code and "exit 0" not in code


def test_workflow_single_line_run_values_are_valid_yaml() -> None:
    """Ein ``: `` in einem ungequoteten einzeiligen Wert macht die Datei zu ungültigem YAML."""
    for number, line in enumerate(_workflow().splitlines(), start=1):
        match = re.match(r"^\s*run: (.+)$", line)
        if match is None or match.group(1)[0] in "'\"|>":
            continue
        assert ": " not in match.group(1), f"Zeile {number}: Wert quoten ({line.strip()})"
        assert " #" not in match.group(1), f"Zeile {number}: „ #“ beginnt einen Kommentar"


def test_workflow_runs_one_command_per_step() -> None:
    """PowerShell wertet je Schritt nur den letzten Exit-Code aus; also ein Befehl je Schritt."""
    blocks = re.findall(r"run: \|\n((?:\s{10}.*\n)+)", _workflow())
    assert len(blocks) == 1, "nur der Signatur-Wächter darf mehrzeilig sein"
    assert "CODE SIGNING: NOT CONFIGURED" in blocks[0] and "exit 1" in blocks[0]


def test_workflow_actions_are_pinned_to_commits() -> None:
    uses = re.findall(r"uses: (\S+)", _workflow())
    assert uses
    for action in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", action), action


def test_workflow_contains_the_complete_build_in_order() -> None:
    text = _workflow()
    commands = [
        "requirements/build-windows.lock",
        "requirements/ci-tools.lock",
        "build.py check-env",
        "ruff check src tests tools",
        "ruff format --check src tests tools",
        "mypy --strict",
        "pytest -q",
        "build.py prepare --build-number",
        "build.py prepare --release --build-number",
        "build.py freeze",
        "build.py audit",
        "build.py selftest",
        "build.py msi",
        "build.py verify",
        "build.py manifest",
    ]
    positions = [text.index(command) for command in commands]
    assert positions == sorted(positions)
    assert "if-no-files-found: error" in text
    assert "if: always()" in text


def test_workflow_separates_test_and_release_builds() -> None:
    text = _workflow()
    assert "options: [test, release]" in text and "default: test" in text
    # Release nur über Tag v…; Branch-Pushes und Pull Requests bleiben Testbuilds.
    assert (
        "startsWith(github.ref, 'refs/tags/v') && 'release' || inputs.build_type || 'test'" in text
    )
    assert "github.event_name == 'push' && 'release'" not in text
    assert "if: env.BUILD_TYPE == 'release' && vars.SIGNING_ENABLED != 'true'" in text
    signing = re.findall(r"if: (.*)\n\s+uses: azure/trusted-signing-action", text)
    assert signing == ["env.BUILD_TYPE == 'release' && vars.SIGNING_ENABLED == 'true'"] * 2
    assert "visibility == 'public' || vars.ATTESTATION_ENABLED == 'true'" in text


def test_workflow_and_script_use_the_same_wix_version() -> None:
    workflow = re.search(r'WIX_VERSION: "([\d.]+)"', _workflow())
    script = re.search(r"\$ExpectedWix = '([\d.]+)'", SCRIPT.read_text(encoding="utf-8-sig"))
    assert workflow and script and workflow.group(1) == script.group(1)


# ---------- PowerShell-Skript ----------


def test_script_encoding_for_windows_powershell() -> None:
    data = SCRIPT.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf"), "UTF-8 mit BOM, sonst zerstört PowerShell 5.1 Umlaute"
    assert data.count(b"\n") == data.count(b"\r\n"), "Windows-Zeilenenden"


def test_script_fails_fast_and_is_marked_untested() -> None:
    text = SCRIPT.read_text(encoding="utf-8-sig")
    assert "NOT TESTED ON WINDOWS" in text
    assert "Set-StrictMode -Version Latest" in text and "$ErrorActionPreference = 'Stop'" in text
    assert "if ($LASTEXITCODE -ne 0)" in text and "exit 1" in text
    for step in (
        "check-env",
        "'test'",
        "'prepare'",
        "'freeze'",
        "'audit'",
        "'selftest'",
        "'msi'",
        "'verify'",
        "'manifest'",
        "Get-FileHash",
    ):
        assert step in text, step


def test_script_contains_no_secrets_and_no_unpinned_pip_upgrade() -> None:
    text = _code(SCRIPT.read_text(encoding="utf-8-sig")).lower()
    for forbidden in ("--upgrade", "password=", "begin private key", "icw_secret_", "token="):
        assert forbidden not in text, forbidden


# ---------- build.py: Testbuild und Release ----------


@pytest.fixture
def staged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(build, "BUILD", tmp_path / "build")
    (tmp_path / "build").mkdir()
    return tmp_path


def _context(dist: Path, *, release: bool = False) -> object:
    return build.Context(release, "7", dist, False, release)


def _stage(staged: Path, kind: str) -> None:
    (staged / "build" / "build_type").write_text(kind, encoding="utf-8")


def test_build_type_is_required_and_validated(staged: Path) -> None:
    with pytest.raises(build.BuildError, match="zuerst „prepare“"):
        build.build_type()
    _stage(staged, "nightly")
    with pytest.raises(build.BuildError, match="Unbekannte Build-Art"):
        build.build_type()


def test_test_builds_are_named_unmistakably(staged: Path) -> None:
    _stage(staged, "test")
    assert build.msi_path(_context(staged)).name.endswith("-x64-TESTBUILD.msi")
    _stage(staged, "release")
    assert build.msi_path(_context(staged)).name.endswith("-x64.msi")


def test_release_installer_refuses_placeholder_license(staged: Path) -> None:
    _stage(staged, "release")
    assert build.PLACEHOLDER in build.LICENSE_RTF.read_text(encoding="utf-8")
    with pytest.raises(build.BuildError, match="Platzhalter-Lizenz"):
        build.build_msi(_context(staged, release=True))


def test_release_implies_required_signature(
    staged: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ICW_SIGN_COMMAND", raising=False)
    assert build.main(["sign-exe", "--release", "--dist", str(staged)]) == 2
    assert "Signatur verlangt" in capsys.readouterr().err
    assert build.main(["sign-exe", "--dist", str(staged)]) == 0


# ---------- build.py: MSI-Prüfung ----------


def _msi(path: Path, *, size: int = 0, magic: bytes = build.OLE_SIGNATURE) -> Path:
    with path.open("wb") as handle:
        handle.write(magic)
        handle.seek((size or build.MIN_MSI_BYTES) - 1)
        handle.write(b"\0")
    return path


def _properties(**changes: str) -> dict[str, str]:
    found = {
        "ProductName": build.PRODUCT_NAME,
        "ProductVersion": build.project_version().msi_version(),
        "Manufacturer": build.PUBLISHER,
        "UpgradeCode": "{" + build.upgrade_code().lower() + "}",
        "ARPCONTACT": "x",
    }
    found.update(changes)
    return found


def test_check_msi_accepts_a_correct_test_build(tmp_path: Path) -> None:
    path = _msi(tmp_path / "a.msi")
    lines = build.check_msi(path, "test", lambda _p: _properties(), lambda _p: "NotSigned")
    assert "Build-Art: TESTBUILD (nur intern)" in lines and "Signatur: NotSigned" in lines


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        (lambda p: p, "MSI fehlt"),
        (lambda p: _msi(p, size=1024), "MSI-Größe unplausibel"),
        (lambda p: _msi(p, magic=b"MZ\x90\x00\x03\x00\x00\x00"), "keine MSI-Datei"),
    ],
)
def test_check_msi_rejects_broken_files(tmp_path: Path, setup: object, message: str) -> None:
    path = setup(tmp_path / "b.msi")  # type: ignore[operator]
    with pytest.raises(build.BuildError, match=message):
        build.check_msi(path, "test", lambda _p: _properties(), lambda _p: "NotSigned")


def test_check_msi_rejects_wrong_properties(tmp_path: Path) -> None:
    path = _msi(tmp_path / "c.msi")
    with pytest.raises(build.BuildError, match="ProductVersion"):
        build.check_msi(
            path, "test", lambda _p: _properties(ProductVersion="1.0.0"), lambda _p: "x"
        )


def test_release_msi_must_be_validly_signed(tmp_path: Path) -> None:
    path = _msi(tmp_path / "d.msi")
    with pytest.raises(build.BuildError, match="nicht gültig signiert"):
        build.check_msi(path, "release", lambda _p: _properties(), lambda _p: "NotSigned")
    lines = build.check_msi(path, "release", lambda _p: _properties(), lambda _p: "Valid")
    assert "Build-Art: RELEASE" in lines


def test_msi_properties_need_windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build, "WINDOWS", False)
    with pytest.raises(build.BuildError, match="nur unter Windows"):
        build.msi_properties(tmp_path / "e.msi")


def test_property_table_output_is_parsed() -> None:
    text = "ProductName=IC-Ware Auftrags-Import\r\nProductVersion=2.0.0\r\nLeer=\r\nkaputt\r\n"
    assert build.parse_properties(text) == {
        "ProductName": "IC-Ware Auftrags-Import",
        "ProductVersion": "2.0.0",
        "Leer": "",
    }
