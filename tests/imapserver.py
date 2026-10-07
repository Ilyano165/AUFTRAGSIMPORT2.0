"""Lokaler, reproduzierbarer IMAP-Server (Dovecot) für Integrationstests und manuelle Tests.

- Eigene Test-CA und Serverzertifikat nur für ``localhost`` (Hostnamenprüfung testbar).
- Implizites TLS und STARTTLS; Anmeldung ohne TLS ist verboten (wie bei Hostern üblich).
- Dovecot läuft im Vordergrund (``-F``) als Kindprozess mit umgeleiteten Ein-/Ausgaben.
  Ein selbst daemonisierender Dovecot erbt sonst die Ausgabekanäle des Aufrufers und hält
  aufrufende Werkzeuge offen; genau das hat den ersten Einrichtungsversuch blockiert.
- Jeder Aufruf von ``doveadm`` hat ein Zeitlimit.
- Jeder Test erhält einen eigenen Benutzer, also ein eigenes, leeres Postfach. Die
  Benutzerdatenbank ist statisch (jeder Name gültig, ein Testpasswort): Dovecot liest eine
  passwd-Datei im laufenden Betrieb nicht zuverlässig neu, später ergänzte Benutzer fehlten.

Vertrauen in die Test-CA erhalten Tests über ``SSL_CERT_FILE``: Der Produktivcode prüft
Zertifikate unverändert gegen den Systemspeicher.
"""

from __future__ import annotations

import atexit
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

TIMEOUT = 20
PASSWORD = "Geheim-Passwort-123"

_CA_EXT = "basicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,cRLSign\n"
_SERVER_EXT = (
    "basicConstraints=critical,CA:FALSE\n"
    "keyUsage=critical,digitalSignature,keyEncipherment\n"
    "extendedKeyUsage=serverAuth\n"
    "subjectAltName={san}\n"
    "subjectKeyIdentifier=hash\n"
    "authorityKeyIdentifier=keyid,issuer\n"
)
_CONFIG = """base_dir = {d}/run
state_dir = {d}/state
log_path = {d}/dovecot.log
protocols = imap
listen = {listen}
ssl = yes
ssl_cert = <{d}/server.crt
ssl_key = <{d}/server.key
ssl_min_protocol = TLSv1.2
disable_plaintext_auth = yes
auth_mechanisms = plain login
mail_location = maildir:{d}/mail/%u
first_valid_uid = 1
passdb {{
  driver = static
  args = password={password}
}}
userdb {{
  driver = static
  args = uid=nobody gid={group} home={d}/home/%u allow_all_users=yes
}}
service imap-login {{
  chroot =
  inet_listener imap {{
    address = {listen}
    port = {imap}
  }}
  inet_listener imaps {{
    address = {listen}
    port = {imaps}
    ssl = yes
  }}
}}
service anvil {{
  chroot =
}}
"""


def unavailable_reason() -> str:
    """Leer, wenn ein Dovecot-Test möglich ist; sonst der Grund."""
    if sys.platform != "linux":
        return "Dovecot-Integrationstests laufen nur unter Linux"
    missing = [tool for tool in ("dovecot", "doveadm", "openssl") if shutil.which(tool) is None]
    if missing:
        return f"nicht installiert: {', '.join(missing)}"
    if os.geteuid() != 0:
        return "Dovecot-Testserver braucht root (Benutzerwechsel der Mailprozesse)"
    return ""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run(args: list[str], data: bytes | None = None) -> str:
    result = subprocess.run(
        args,
        input=data,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=TIMEOUT,
        check=False,
        **({} if data is not None else {"stdin": subprocess.DEVNULL}),
    )
    output = result.stdout.decode("utf-8", "replace")
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:4])} … fehlgeschlagen: {output.strip()[:300]}")
    return output


@dataclass
class DovecotServer:
    """Dovecot-Instanz in einem eigenen Ordner."""

    base: Path
    listen: str = "127.0.0.1"
    names: tuple[str, ...] = ()
    imap_port: int = 0
    imaps_port: int = 0
    process: subprocess.Popen[bytes] | None = None
    users: list[str] = field(default_factory=list)

    @property
    def config(self) -> Path:
        return self.base / "dovecot.conf"

    @property
    def ca_file(self) -> Path:
        return self.base / "ca.crt"

    def _certificates(self) -> None:
        b = self.base
        (b / "ca.ext").write_text(_CA_EXT)
        (b / "server.ext").write_text(_SERVER_EXT.format(san=self.subject_alt_names()))
        _run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "2",
                "-keyout",
                str(b / "ca.key"),
                "-out",
                str(b / "ca.crt"),
                "-subj",
                "/CN=IC-Ware IMAP Test-CA",
                "-addext",
                "basicConstraints=critical,CA:TRUE",
                "-addext",
                "keyUsage=critical,keyCertSign,cRLSign",
            ]
        )
        _run(
            [
                "openssl",
                "req",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-subj",
                "/CN=localhost",
                "-keyout",
                str(b / "server.key"),
                "-out",
                str(b / "server.csr"),
            ]
        )
        _run(
            [
                "openssl",
                "x509",
                "-req",
                "-days",
                "2",
                "-in",
                str(b / "server.csr"),
                "-CA",
                str(b / "ca.crt"),
                "-CAkey",
                str(b / "ca.key"),
                "-CAcreateserial",
                "-out",
                str(b / "server.crt"),
                "-extfile",
                str(b / "server.ext"),
            ]
        )

    @classmethod
    def create(
        cls,
        *,
        listen: str = "127.0.0.1",
        names: tuple[str, ...] = (),
        ports: tuple[int, int] = (0, 0),
    ) -> DovecotServer:
        """Eigener, für Dovecots Dienstbenutzer betretbarer Ordner (pytest-Ordner sind 0700).

        ``listen``/``names``/``ports`` machen den Server von einem anderen Rechner (Windows-VM)
        erreichbar: Adresse, zusätzliche Namen oder IPs im Zertifikat, feste Ports
        (STARTTLS, SSL/TLS; 0 = frei wählen).
        """
        base = Path(tempfile.mkdtemp(prefix="icware-dovecot-"))
        base.chmod(0o755)
        return cls(base, listen=listen, names=names, imap_port=ports[0], imaps_port=ports[1])

    def subject_alt_names(self) -> str:
        """``DNS:localhost`` plus weitere Namen (DNS) oder Adressen (IP)."""
        entries = ["DNS:localhost"]
        for name in self.names:
            kind = "IP" if _is_address(name) else "DNS"
            entries.append(f"{kind}:{name}")
        return ",".join(entries)

    def start(self) -> DovecotServer:
        """Zertifikate, Konfiguration, Prozess; wartet, bis beide Ports annehmen."""
        for sub in ("run", "state", "mail", "home"):
            (self.base / sub).mkdir(parents=True, exist_ok=True)
        self._certificates()
        group = "nogroup" if _group_exists("nogroup") else "nobody"
        for sub in ("mail", "home"):
            shutil.chown(self.base / sub, "nobody", group)
        self.imap_port = self.imap_port or _free_port()
        self.imaps_port = self.imaps_port or _free_port()
        self.config.write_text(
            _CONFIG.format(
                d=self.base,
                group=group,
                listen=self.listen,
                imap=self.imap_port,
                imaps=self.imaps_port,
                password=PASSWORD,
            )
        )
        self.process = subprocess.Popen(
            ["dovecot", "-F", "-c", str(self.config)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        atexit.register(self.stop)
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"Dovecot beendet: {self.log()[-500:]}")
            probe = "127.0.0.1" if self.listen in ("0.0.0.0", "*") else self.listen
            if all(_accepts(probe, port) for port in (self.imap_port, self.imaps_port)):
                return self
            time.sleep(0.1)
        self.stop()
        raise RuntimeError("Dovecot nimmt keine Verbindungen an")

    def stop(self) -> None:
        """Beendet den Prozess (erst höflich, dann hart) und löscht den Ordner."""
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=10)
        self.process = None
        shutil.rmtree(self.base, ignore_errors=True)

    def log(self) -> str:
        path = self.base / "dovecot.log"
        return path.read_text(errors="replace") if path.exists() else ""

    def add_user(self, name: str) -> str:
        """Neuer Benutzer mit leerem Postfach (statische Benutzerdatenbank, ``PASSWORD``)."""
        if name in self.users:
            raise ValueError(f"Benutzer {name} existiert bereits")
        self.users.append(name)
        return name

    def doveadm(self, *args: str, data: bytes | None = None) -> str:
        return _run(["doveadm", "-c", str(self.config), *args], data)

    def save(self, user: str, raw: bytes, mailbox: str = "INBOX") -> None:
        """Legt eine Mail ab (UIDs aufsteigend in Ablagereihenfolge)."""
        self.doveadm("save", "-u", user, "-m", mailbox, data=raw)

    def create_mailbox(self, user: str, mailbox: str) -> None:
        self.doveadm("mailbox", "create", "-u", user, mailbox)

    def add_flags(self, user: str, uid: int, flags: str, mailbox: str = "INBOX") -> None:
        self.doveadm("flags", "add", "-u", user, flags, "mailbox", mailbox, "uid", str(uid))

    def set_uidvalidity(self, user: str, mailbox: str, value: int) -> None:
        self.doveadm("mailbox", "update", "--uid-validity", str(value), "-u", user, mailbox)

    def flags(self, user: str, mailbox: str = "INBOX") -> dict[int, frozenset[str]]:
        """Markierungen je UID, direkt aus dem Server (ohne IMAP)."""
        output = self.doveadm("fetch", "-u", user, "uid flags", "mailbox", mailbox)
        found: dict[int, frozenset[str]] = {}
        uid = 0
        for line in output.splitlines():
            if line.startswith("uid:"):
                uid = int(line.split(":", 1)[1])
            elif line.startswith("flags:"):
                found[uid] = frozenset(line.split(":", 1)[1].split())
        return found

    def message_count(self, user: str, mailbox: str = "INBOX") -> int:
        return len(self.flags(user, mailbox))


def _group_exists(name: str) -> bool:
    import grp  # noqa: PLC0415

    try:
        grp.getgrnam(name)
    except KeyError:
        return False
    return True


def _is_address(text: str) -> bool:
    import ipaddress  # noqa: PLC0415

    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


def _accepts(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False
