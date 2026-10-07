# Updates

## Heute: Verteilung über die IT

Neue Versionen kommen als signiertes MSI und werden wie in `installer.md` beschrieben installiert,
interaktiv oder still über Intune, Gruppenrichtlinie oder Softwareverteilung. Das MSI-Upgrade ist
dafür gebaut; Benutzerdaten bleiben erhalten.

## Vorbereitet, aber bewusst ohne Netzwerk

- `services/updates.py`: Manifest-Schema (Kanal, Version, Mindestversion, Datum, Hinweise,
  Download-Seite, MSI-Name, SHA-256, Größe), strenge Prüfung (nur https, nur IC-Ware-Hosts,
  Prüfsumme und Dateiname formal geprüft) und SemVer-Vergleich mit den Ergebnissen
  „aktuell“, „Update verfügbar“, „Update erforderlich“; Vorabversionen nur im Kanal „beta“.
- Richtlinie `updates.check_enabled` ist standardmäßig aus und nur mit https-Manifest-Adresse wirksam.

Nicht gebaut ist der Download. Ein selbstgebauter Downloader wäre angreifbar: zurückgespielte
alte Versionen, eingefrorene Metadaten, vertauschte Dateien, kompromittierter Server.

## Zielentwurf für Updates aus der Anwendung

1. Metadaten nach The Update Framework (python-tuf, etwa über `tufup`): signierte Rollen (root,
   targets, snapshot, timestamp), fest eingebauter Root-Schlüssel, Schutz gegen Rollback und
   eingefrorene Metadaten, Schlüsselrotation.
2. Download nur der in den signierten Metadaten genannten Datei mit Größen- und SHA-256-Prüfung.
3. Authenticode-Prüfung des MSI (WinVerifyTrust, Herausgeber IC-Ware) vor jeder Ausführung.
4. Installation nur nach Bestätigung durch den Benutzer über `msiexec` (UAC); die laufende EXE
   ersetzt sich nie selbst.
5. Über die Richtlinie abschaltbar, damit verwaltete Umgebungen bei der Softwareverteilung bleiben.

## Beispielmanifest

```json
{
  "format": "icware-update-manifest",
  "channel": "stable",
  "version": "2.0.2",
  "released": "2026-11-02",
  "minimum_version": "2.0.0",
  "notes_url": "https://ic-ware.eu/auftrags-import/2.0.2",
  "download_page": "https://downloads.ic-ware.eu/auftrags-import",
  "msi": {"name": "IC-Ware-AuftragsImport-2.0.2-x64.msi", "sha256": "<64 Hex-Zeichen>", "size": 80000000}
}
```
