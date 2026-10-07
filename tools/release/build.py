"""Reproduzierbarer Windows-Build: ein Befehl, keine Handgriffe.

    python tools/release/build.py all --release --build-number 57

Einzelschritte: prepare, test, freeze, audit, selftest, sign-exe, msi, sign-msi, manifest.
Hilfsbefehl: compare <ordner-a> <ordner-b> (Reproduzierbarkeit prüfen).

Reproduzierbar heißt hier: gleiche Eingaben (Commit, gesperrte Abhängigkeiten mit Prüfsummen,
gepinnte Python- und Werkzeugversionen) ergeben dasselbe Programm. Der Zeitstempel ist der
Commit-Zeitpunkt (SOURCE_DATE_EPOCH), PYTHONHASHSEED ist fest. Das MSI ist absichtlich nicht
bitgleich: Windows Installer verlangt je Paket einen neuen PackageCode.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import struct
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
PACKAGE = SRC / "icware_auftragsimport"
BUILD = ROOT / "build"
APP = "AuftragsImport"
SPEC = ROOT / "packaging" / "pyinstaller" / f"{APP}.spec"
WXS = ROOT / "packaging" / "wix" / f"{APP}.wxs"
LICENSE_RTF = ROOT / "packaging" / "wix" / "License.rtf"
ICON = ROOT / "packaging" / "windows" / f"{APP}.ico"
LOCK = ROOT / "requirements" / "build-windows.lock"
PLACEHOLDER = "PLATZHALTER"
WINDOWS = sys.platform.startswith("win")
BUILD_TYPES = ("test", "release")
# Plausibilitätsgrenzen, nicht gemessen: ohne Qt-Bibliotheken wäre die MSI deutlich kleiner.
MIN_MSI_BYTES = 15 * 1024 * 1024
MAX_MSI_BYTES = 400 * 1024 * 1024
OLE_SIGNATURE = bytes.fromhex("d0cf11e0a1b11ae1")  # MSI = OLE-Verbunddokument
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import audit as bundle_audit  # noqa: E402

from icware_auftragsimport.branding import PRODUCT_NAME, PUBLISHER, SUPPORT_CONTACT  # noqa: E402
from icware_auftragsimport.buildinfo import UNVERSIONED, BuildInfo, render_module  # noqa: E402
from icware_auftragsimport.versioning import Version  # noqa: E402


class BuildError(RuntimeError):
    """Abbruch mit verständlicher Begründung."""


@dataclass(frozen=True)
class Context:
    """Einstellungen eines Laufs."""

    release: bool
    build_number: str
    dist: Path
    allow_dirty: bool
    require_signature: bool


def step(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


def run(command: list[str | Path], *, env: dict[str, str] | None = None) -> None:
    printable = " ".join(str(part) for part in command)
    print(f"$ {printable}", flush=True)
    result = subprocess.run([str(part) for part in command], cwd=ROOT, env=env, check=False)
    if result.returncode != 0:
        raise BuildError(f"Befehl fehlgeschlagen ({result.returncode}): {printable}")


def git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def project_version() -> Version:
    text = (PACKAGE / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__ = "([^"]+)"', text)
    if match is None:
        raise BuildError("__version__ fehlt in src/icware_auftragsimport/__init__.py")
    return Version.parse(match.group(1))


def source_date_epoch(commit: str | None) -> int:
    if os.environ.get("SOURCE_DATE_EPOCH", "").isdigit():
        return int(os.environ["SOURCE_DATE_EPOCH"])
    stamp = git("log", "-1", "--format=%ct") if commit else None
    return int(stamp) if stamp and stamp.isdigit() else int(datetime.now(UTC).timestamp())


def locked_versions() -> dict[str, str]:
    pins = re.findall(
        r"^([a-z0-9][a-z0-9._-]*)==([^\s\\]+)", LOCK.read_text(encoding="utf-8"), re.MULTILINE
    )
    return {name: version for name, version in pins}


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def environment_problems() -> list[str]:
    problems = []
    wanted = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    if platform.python_version() != wanted:
        problems.append(f"Python {platform.python_version()} statt {wanted} (.python-version)")
    for name, version in locked_versions().items():
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            problems.append(f"{name}=={version} fehlt (requirements/build-windows.lock)")
            continue
        if installed != version:
            problems.append(f"{name} {installed} installiert, gesperrt ist {version}")
    return problems


def check_environment() -> None:
    """Build-Umgebung: Python laut .python-version, 64 Bit, Pakete exakt laut Sperrdatei."""
    step("Build-Umgebung")
    problems = environment_problems()
    bits = struct.calcsize("P") * 8
    if bits != 64:
        problems.append(f"Python mit {bits} Bit; der Build braucht 64 Bit")
    machine, system = platform.machine(), platform.platform()
    print(f"Python {platform.python_version()} ({machine}, {bits} Bit), {system}")
    if problems:
        raise BuildError("Build-Umgebung passt nicht:\n- " + "\n- ".join(problems))
    print("Python-Version und gesperrte Pakete stimmen.")


def version_resource(version: Version, build: int, year: int) -> str:
    """Windows-Versionsressource der EXE (Dateiversion mit Build-Nummer, Produktversion ohne)."""
    file_version = version.file_version(build)
    dotted = ".".join(str(v) for v in file_version)
    strings = {
        "CompanyName": PUBLISHER,
        "FileDescription": PRODUCT_NAME,
        "FileVersion": dotted,
        "InternalName": APP,
        "LegalCopyright": f"© {year} {PUBLISHER}",
        "OriginalFilename": f"{APP}.exe",
        "ProductName": PRODUCT_NAME,
        "ProductVersion": str(version),
    }
    table = ", ".join(f"StringStruct({k!r}, {v!r})" for k, v in strings.items())
    return (
        "VSVersionInfo(\n"
        f"  ffi=FixedFileInfo(filevers={file_version}, prodvers={file_version},\n"
        "    mask=0x3f, flags=0x0,\n"
        "    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),\n"
        f"  kids=[StringFileInfo([StringTable('040704B0', [{table}])]),\n"
        "        VarFileInfo([VarStruct('Translation', [1031, 1200])])]\n)\n"
    )


def prepare(ctx: Context) -> BuildInfo:
    step("Vorbereitung")
    version = project_version()
    commit = git("rev-parse", "HEAD")
    dirty = bool(git("status", "--porcelain")) if commit else False
    if ctx.release:
        problems = environment_problems()
        if commit is None:
            problems.append("Kein Git-Repository: ein Release braucht einen Commit")
        if dirty and not ctx.allow_dirty:
            problems.append("Nicht eingecheckte Änderungen im Arbeitsverzeichnis")
        if f"## [{version}]" not in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"):
            problems.append(
                f"CHANGELOG.md hat keinen Abschnitt „## [{version}]“ (tools/release/version.py)"
            )
        tag = os.environ.get("GITHUB_REF_NAME", "")
        if tag.startswith("v") and tag != f"v{version}":
            problems.append(f"Tag {tag} passt nicht zur Version {version}")
        if PLACEHOLDER in LICENSE_RTF.read_text(encoding="utf-8"):
            problems.append("packaging/wix/License.rtf ist noch ein Platzhalter (EULA fehlt)")
        if not ctx.build_number.isdigit():
            problems.append("Release braucht eine numerische Build-Nummer (--build-number)")
        if problems:
            raise BuildError("Release nicht möglich:\n- " + "\n- ".join(problems))
    epoch = source_date_epoch(commit)
    info = BuildInfo(
        str(version),
        ctx.build_number,
        commit or UNVERSIONED,
        datetime.fromtimestamp(epoch, UTC),
        dirty,
    )
    BUILD.mkdir(exist_ok=True)
    kind = "release" if ctx.release else "test"
    (BUILD / "build_type").write_text(kind, encoding="utf-8")
    (PACKAGE / "_build_info.py").write_text(render_module(info), encoding="utf-8")
    number = int(ctx.build_number) if ctx.build_number.isdigit() else 0
    (BUILD / "version_info.txt").write_text(
        version_resource(version, number, info.timestamp.year), encoding="utf-8"
    )  # type: ignore[union-attr]
    (BUILD / "source_date_epoch").write_text(str(epoch), encoding="utf-8")
    print(f"{PRODUCT_NAME} {info.describe()}  (MSI-Version {version.msi_version()})")
    if kind == "test":
        print("Build-Art: TESTBUILD – unsigniert zulässig, Platzhalter-Lizenz zulässig, nur intern")
    else:
        print("Build-Art: RELEASE – Signatur und endgültige Lizenz erforderlich")
    return info


def build_type() -> str:
    """Build-Art aus „prepare“: ``test`` oder ``release``."""
    path = BUILD / "build_type"
    if not path.exists():
        raise BuildError("Build-Art unbekannt; zuerst „prepare“ ausführen")
    kind = path.read_text(encoding="utf-8").strip()
    if kind not in BUILD_TYPES:
        raise BuildError(f"Unbekannte Build-Art „{kind}“ in {path}")
    return kind


def test() -> None:
    step("Prüfungen")
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONPATH": str(SRC)}
    run([sys.executable, "-m", "ruff", "check", "src", "tests", "tools"], env=env)
    run([sys.executable, "-m", "ruff", "format", "--check", "src", "tests", "tools"], env=env)
    run([sys.executable, "-m", "mypy", "--strict"], env=env)
    run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], env=env)


def _epoch() -> str:
    path = BUILD / "source_date_epoch"
    if not path.exists():
        raise BuildError("Zuerst „prepare“ ausführen")
    return path.read_text(encoding="utf-8").strip()


def collect_licenses(target: Path) -> None:
    """Lizenztexte aller ausgelieferten Pakete (Pflicht u. a. für Qt/PySide6 unter LGPL)."""
    target.mkdir(parents=True, exist_ok=True)
    lines = [f"Drittanbieter-Komponenten von {PRODUCT_NAME}", ""]
    shipped = {_canonical(n) for n in locked_versions()} - {
        "pyinstaller",
        "pyinstaller-hooks-contrib",
        "altgraph",
        "pefile",
        "setuptools",
        "packaging",
    }
    for dist in sorted(metadata.distributions(), key=lambda d: _canonical(d.metadata["Name"])):
        name = _canonical(dist.metadata["Name"])
        if name not in shipped:
            continue
        license_name = (
            dist.metadata.get("License-Expression")
            or dist.metadata.get("License")
            or "siehe Lizenztext"
        )
        lines.append(
            f"{dist.metadata['Name']} {dist.version}: {license_name.splitlines()[0][:120]}"
        )
        for file in dist.files or []:
            if re.match(r"(LICEN[CS]E|COPYING|NOTICE)", Path(str(file)).name, re.IGNORECASE):
                source = Path(str(dist.locate_file(file)))
                if source.is_file():
                    folder = target / name
                    folder.mkdir(exist_ok=True)
                    shutil.copyfile(source, folder / Path(str(file)).name)
    lines += [
        "",
        "Qt und PySide6 stehen unter der GNU LGPL v3. Die Bibliotheken liegen als einzelne",
        "Dateien im Programmordner und dürfen gemäß LGPL durch kompatible ersetzt werden.",
    ]
    (target.parent / "DRITTANBIETER.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def freeze(ctx: Context) -> Path:
    step("Programm erzeugen (PyInstaller)")
    env = {
        **os.environ,
        "PYTHONHASHSEED": "0",
        "SOURCE_DATE_EPOCH": _epoch(),
        "PYTHONPATH": str(SRC),
    }
    output = ctx.dist / APP
    shutil.rmtree(output, ignore_errors=True)
    shutil.rmtree(BUILD / "pyinstaller", ignore_errors=True)
    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--clean",
            "--noconfirm",
            "--distpath",
            ctx.dist,
            "--workpath",
            BUILD / "pyinstaller",
            SPEC,
        ],
        env=env,
    )
    docs = output / "docs"
    docs.mkdir(exist_ok=True)
    shutil.copyfile(
        ROOT / "packaging" / "windows" / "policy.example.json", docs / "Richtlinie-Beispiel.json"
    )
    shutil.copyfile(ROOT / "LICENSE", docs / "LIZENZ.txt")
    collect_licenses(docs / "drittanbieter")
    return output


def audit(ctx: Context) -> None:
    step("Produktprüfung (keine Entwicklungsdateien)")
    bundle = ctx.dist / APP
    if not bundle.exists():
        raise BuildError(f"{bundle} fehlt; zuerst „freeze“")
    problems = bundle_audit.audit(bundle, BUILD / "pyinstaller" / APP, ROOT)
    if problems:
        raise BuildError("Entwicklungsdateien im Produkt:\n- " + "\n- ".join(problems[:40]))
    print("Keine Tests, Secrets, Datenbanken, Build-Pfade oder Entwicklerwerkzeuge im Produkt.")


def executable(ctx: Context) -> Path:
    return ctx.dist / APP / (f"{APP}.exe" if WINDOWS else APP)


def selftest(ctx: Context) -> None:
    step("Selbsttest der fertigen Anwendung")
    exe = executable(ctx)
    if not exe.exists():
        raise BuildError(f"{exe} fehlt; zuerst „freeze“")
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    env.pop("PYTHONPATH", None)
    version_file, report = BUILD / "version.txt", BUILD / "selftest.txt"
    run([exe, "--version", "--output", version_file], env=env)
    expected = (PACKAGE / "_build_info.py").read_text(encoding="utf-8")
    found = version_file.read_text(encoding="utf-8")
    if str(project_version()) not in found or not re.search(r"COMMIT = '([^']*)'", expected):
        raise BuildError(f"Versionsausgabe passt nicht: {found}")
    try:
        run([exe, "--self-test", "--output", report], env=env)
    finally:
        if report.exists():
            print(report.read_text(encoding="utf-8"))


def sign(ctx: Context, files: list[Path]) -> bool:
    """Signiert über ICW_SIGN_COMMAND (Vorlage mit {file}); siehe docs/release/signing.md."""
    template = os.environ.get("ICW_SIGN_COMMAND", "")
    step("Signieren")
    if not template:
        if ctx.require_signature:
            raise BuildError("Signatur verlangt, aber ICW_SIGN_COMMAND ist nicht gesetzt")
        print("WARNUNG: unsigniert – nicht an Kunden ausliefern (RELEASE_CHECKLIST.md)")
        return False
    for file in files:
        run(shlex.split(template.format(file=file), posix=not WINDOWS))
        verify = os.environ.get("ICW_VERIFY_COMMAND", "")
        if verify:
            run(shlex.split(verify.format(file=file), posix=not WINDOWS))
    return True


def msi_path(ctx: Context) -> Path:
    """Release ``…-x64.msi``, Testbuild ``…-x64-TESTBUILD.msi``: nie verwechselbar."""
    suffix = "" if build_type() == "release" else "-TESTBUILD"
    return ctx.dist / f"IC-Ware-AuftragsImport-{project_version()}-x64{suffix}.msi"


def build_msi(ctx: Context) -> Path:
    step("Installer erzeugen (WiX)")
    if build_type() == "release" and PLACEHOLDER in LICENSE_RTF.read_text(encoding="utf-8"):
        raise BuildError("Release-Installer mit Platzhalter-Lizenz verweigert (License.rtf)")
    if not WINDOWS or shutil.which("wix") is None:
        raise BuildError(
            "Das MSI entsteht unter Windows mit WiX (dotnet tool install --global wix)"
        )
    version = project_version()
    target = msi_path(ctx)
    run(
        [
            "wix",
            "build",
            "-arch",
            "x64",
            "-culture",
            "de-DE",
            "-ext",
            "WixToolset.UI.wixext",
            "-d",
            f"ProductVersion={version.msi_version()}",
            "-d",
            f"SourceDir={ctx.dist / APP}",
            "-d",
            f"IconFile={ICON}",
            "-d",
            f"LicenseRtf={LICENSE_RTF}",
            "-d",
            f"SupportContact={SUPPORT_CONTACT}",
            "-o",
            target,
            WXS,
        ]
    )
    return target


def upgrade_code() -> str:
    """UpgradeCode aus der WiX-Definition (in Großbuchstaben, ohne Klammern)."""
    match = re.search(r'UpgradeCode="([0-9A-Fa-f-]{36})"', WXS.read_text(encoding="utf-8"))
    if match is None:
        raise BuildError(f"UpgradeCode fehlt in {WXS}")
    return match.group(1).upper()


def powershell_env(path: Path, base: dict[str, str] | None = None) -> dict[str, str]:
    """Umgebung für Windows PowerShell 5.1 (``powershell``).

    Aus PowerShell 7 (Standard-Shell der GitHub-Runner) geerbt zeigt ``PSModulePath`` auf die
    PS7-Module; 5.1 kann dann u. a. ``Microsoft.PowerShell.Security`` (Get-AuthenticodeSignature)
    nicht laden. Ohne die Variable setzt 5.1 seinen eigenen Pfad.
    """
    env = dict(os.environ if base is None else base)
    for key in [k for k in env if k.upper() == "PSMODULEPATH"]:
        del env[key]
    env["ICW_MSI"] = str(path)
    return env


def _powershell(script: str, path: Path) -> str:
    """Führt ein PowerShell-Skript aus; der Dateipfad kommt über ``ICW_MSI`` (kein Quoting)."""
    if not WINDOWS:
        raise BuildError("MSI-Prüfung ist nur unter Windows möglich")
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            encoded,
        ],
        capture_output=True,
        text=True,
        env=powershell_env(path),
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        raise BuildError(f"PowerShell fehlgeschlagen: {result.stderr.strip()[:400]}")
    return result.stdout


_PROPERTY_SCRIPT = """
$ErrorActionPreference = 'Stop'
$installer = New-Object -ComObject WindowsInstaller.Installer
$db = $installer.GetType().InvokeMember('OpenDatabase', 'InvokeMethod', $null, $installer,
    @($env:ICW_MSI, 0))
$view = $db.GetType().InvokeMember('OpenView', 'InvokeMethod', $null, $db,
    @('SELECT `Property`, `Value` FROM `Property`'))
$view.GetType().InvokeMember('Execute', 'InvokeMethod', $null, $view, $null) | Out-Null
while ($record = $view.GetType().InvokeMember('Fetch', 'InvokeMethod', $null, $view, $null)) {
    $name = $record.GetType().InvokeMember('StringData', 'GetProperty', $null, $record, 1)
    $value = $record.GetType().InvokeMember('StringData', 'GetProperty', $null, $record, 2)
    Write-Output "$name=$value"
}
"""
_SIGNATURE_SCRIPT = """
$ErrorActionPreference = 'Stop'
(Get-AuthenticodeSignature -LiteralPath $env:ICW_MSI).Status.ToString()
"""


def parse_properties(text: str) -> dict[str, str]:
    """Zeilen „Name=Wert“ der Property-Tabelle."""
    found = {}
    for line in text.splitlines():
        name, separator, value = line.strip().partition("=")
        if separator and name:
            found[name] = value
    return found


def msi_properties(path: Path) -> dict[str, str]:
    """Property-Tabelle der MSI über die Windows-Installer-Schnittstelle (nur Windows)."""
    return parse_properties(_powershell(_PROPERTY_SCRIPT, path))


def authenticode_status(path: Path) -> str:
    """Status der Authenticode-Signatur („Valid“, „NotSigned“ …), nur Windows."""
    return _powershell(_SIGNATURE_SCRIPT, path).strip()


def check_msi(
    path: Path,
    kind: str,
    properties: Callable[[Path], dict[str, str]] = msi_properties,
    signature: Callable[[Path], str] = authenticode_status,
) -> list[str]:
    """Prüft die fertige MSI; jede Abweichung bricht den Build ab."""
    if not path.is_file():
        raise BuildError(f"MSI fehlt: {path} – der Installer-Build ist fehlgeschlagen")
    size = path.stat().st_size
    if not MIN_MSI_BYTES <= size <= MAX_MSI_BYTES:
        raise BuildError(f"MSI-Größe unplausibel: {size} Byte ({MIN_MSI_BYTES}–{MAX_MSI_BYTES})")
    with path.open("rb") as handle:
        if handle.read(8) != OLE_SIGNATURE:
            raise BuildError(f"{path.name} ist keine MSI-Datei (Verbunddokument-Signatur fehlt)")
    found = properties(path)
    expected = {
        "ProductName": PRODUCT_NAME,
        "ProductVersion": project_version().msi_version(),
        "Manufacturer": PUBLISHER,
        "UpgradeCode": "{" + upgrade_code() + "}",
    }
    wrong = [
        f"{name}: {found.get(name, 'fehlt')!r} statt {value!r}"
        for name, value in expected.items()
        if found.get(name, "").upper() != value.upper()
    ]
    if wrong:
        raise BuildError("MSI-Eigenschaften passen nicht:\n- " + "\n- ".join(wrong))
    status = signature(path)
    if kind == "release" and status != "Valid":
        raise BuildError(f"Release-MSI ist nicht gültig signiert (Status {status})")
    label = "TESTBUILD (nur intern)" if kind == "test" else "RELEASE"
    return [
        f"Datei: {path.name}",
        f"Größe: {size} Byte",
        "Dateityp: MSI (OLE-Verbunddokument)",
        *(f"{name}: {found[name]}" for name in expected),
        f"Build-Art: {label}",
        f"Signatur: {status}",
    ]


def verify(ctx: Context) -> None:
    step("Installer prüfen")
    lines = check_msi(msi_path(ctx), build_type())
    (BUILD / "msi-verify.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest(ctx: Context, signed: bool) -> None:
    step("Prüfsummen und Build-Manifest")
    artifacts = [p for p in (executable(ctx), msi_path(ctx)) if p.exists()]
    lines = [f"{sha256(p)}  {p.relative_to(ctx.dist).as_posix()}" for p in artifacts]
    (ctx.dist / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    info = (PACKAGE / "_build_info.py").read_text(encoding="utf-8")
    tools = {"python": platform.python_version()}
    for name in ("pyinstaller", "pyside6-essentials"):
        try:
            tools[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            tools[name] = "fehlt"
    data = {
        "product": PRODUCT_NAME,
        "build_type": build_type(),
        "version": str(project_version()),
        "msi_version": project_version().msi_version(),
        "build_info": info,
        "source_date_epoch": _epoch(),
        "platform": platform.platform(),
        "tools": tools,
        "locks": {p.name: sha256(p) for p in sorted((ROOT / "requirements").glob("*.lock"))},
        "artifacts": {p.relative_to(ctx.dist).as_posix(): sha256(p) for p in artifacts},
        "signed": signed,
    }
    (ctx.dist / "build-manifest.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print("\n".join(lines))


def compare(first: Path, second: Path) -> int:
    """Vergleicht zwei Programmordner Datei für Datei."""

    def tree(base: Path) -> dict[str, str]:
        return {
            p.relative_to(base).as_posix(): sha256(p)
            for p in sorted(base.rglob("*"))
            if p.is_file()
        }

    a, b = tree(first), tree(second)
    differing = sorted(k for k in a.keys() | b.keys() if a.get(k) != b.get(k))
    print(f"{len(a)} Dateien verglichen, {len(differing)} unterschiedlich")
    for name in differing[:50]:
        print(f"  {name}")
    return 1 if differing else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "command",
        choices=[
            "all",
            "check-env",
            "prepare",
            "test",
            "freeze",
            "audit",
            "selftest",
            "sign-exe",
            "msi",
            "sign-msi",
            "verify",
            "manifest",
            "compare",
        ],
    )
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--release", action="store_true", help="alle Release-Prüfungen erzwingen")
    parser.add_argument("--build-number", default=os.environ.get("GITHUB_RUN_NUMBER", "lokal"))
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--require-signature", action="store_true")
    parser.add_argument(
        "--skip-msi", action="store_true", help="ohne Installer (etwa unter Linux zum Testen)"
    )
    args = parser.parse_args(argv)
    ctx = Context(
        args.release,
        str(args.build_number),
        args.dist.resolve(),
        args.allow_dirty,
        args.require_signature or args.release,  # Release ohne Signatur ist nicht zulässig
    )
    try:
        if args.command == "compare":
            return compare(*args.paths)
        actions = {
            "check-env": check_environment,
            "prepare": lambda: prepare(ctx),
            "test": test,
            "freeze": lambda: freeze(ctx),
            "audit": lambda: audit(ctx),
            "selftest": lambda: selftest(ctx),
            "sign-exe": lambda: sign(ctx, [executable(ctx)]),
            "msi": lambda: build_msi(ctx),
            "sign-msi": lambda: sign(ctx, [msi_path(ctx)]),
            "verify": lambda: verify(ctx),
            "manifest": lambda: manifest(ctx, signed=bool(os.environ.get("ICW_SIGN_COMMAND"))),
        }
        if args.command != "all":
            actions[args.command]()
            return 0
        prepare(ctx)
        test()
        freeze(ctx)
        audit(ctx)
        selftest(ctx)
        signed = sign(ctx, [executable(ctx)])
        if not args.skip_msi:
            build_msi(ctx)
            signed = sign(ctx, [msi_path(ctx)]) and signed
            verify(ctx)
        manifest(ctx, signed)
    except BuildError as exc:
        print(f"\nABBRUCH: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
