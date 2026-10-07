"""Lokaler IMAP-Testserver (Dovecot) mit realistischem Mailbestand für manuelle Tests.

    python tools/imap_testserver.py            # Server starten, Bestand laden, auf Enter warten
    python tools/imap_testserver.py --eml DIR  # Bestand nur als .eml-Dateien schreiben

    # Für eine Windows-VM im selben Netz (Linux-Host 192.168.56.1):
    python tools/imap_testserver.py --listen 192.168.56.1 --name 192.168.56.1 \
        --imaps-port 10993 --imap-port 10143

Für die App (Linux, Entwicklung): Postfach mit den ausgegebenen Daten anlegen und die App mit
``SSL_CERT_FILE=<ca.crt>`` starten, damit sie der Test-CA vertraut. Unter Windows ist Dovecot
nicht verfügbar; dort den .eml-Bestand mit ``--demo --testpostfach DIR`` nutzen.
Der Server verändert keine echten Postfächer und ist standardmäßig nur auf 127.0.0.1 erreichbar.
Der Ablauf mit einer Windows-VM ist beschrieben, aber nicht unter Windows getestet.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from imapserver import PASSWORD, DovecotServer, unavailable_reason  # noqa: E402
from mailcorpus import corpus, write_corpus  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """Startet den Server oder schreibt den Bestand als Dateien."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--eml", type=Path, help="Bestand als .eml-Dateien in DIR schreiben")
    parser.add_argument("--user", default="bestellungen", help="Benutzername im Testserver")
    parser.add_argument("--listen", default="127.0.0.1", help="Adresse, auf der der Server lauscht")
    parser.add_argument(
        "--name", action="append", default=[], help="weiterer Name/IP im Zertifikat (mehrfach)"
    )
    parser.add_argument("--imaps-port", type=int, default=0, help="fester SSL/TLS-Port")
    parser.add_argument("--imap-port", type=int, default=0, help="fester STARTTLS-Port")
    args = parser.parse_args(argv)
    if args.eml is not None:
        paths = write_corpus(args.eml)
        print(f"{len(paths)} Testmails in {args.eml}")
        return 0
    reason = unavailable_reason()
    if reason:
        print(f"Testserver nicht verfügbar: {reason}")
        return 2
    server = DovecotServer.create(
        listen=args.listen, names=tuple(args.name), ports=(args.imap_port, args.imaps_port)
    ).start()
    try:
        user = server.add_user(args.user)
        for uid, mail in enumerate(corpus(), start=1):
            server.save(user, mail.raw)
            for flag in mail.flags:
                server.add_flags(user, uid, flag)
        host = args.name[0] if args.name else "localhost"
        print(f"IMAP-Testserver läuft (lauscht auf {args.listen}):")
        print(f"  Server     {host}")
        print(f"  SSL/TLS    Port {server.imaps_port}")
        print(f"  STARTTLS   Port {server.imap_port}")
        print(f"  Benutzer   {user}")
        print(f"  Passwort   {PASSWORD}")
        print(f"  Test-CA    {server.ca_file}  (App mit SSL_CERT_FILE=… starten)")
        print(f"  Bestand    {len(corpus())} Mails")
        print("Windows-Testrechner (nicht unter Windows getestet):")
        print("  1. ca.crt kopieren und importieren: certutil -user -addstore Root ca.crt")
        print("  2. Einstellungen → Postfächer: Server, Port, Benutzer, Passwort wie oben")
        print('  3. Nach dem Test: certutil -user -delstore Root "IC-Ware IMAP Test-CA"')
        input("Enter beendet den Server … ")
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
