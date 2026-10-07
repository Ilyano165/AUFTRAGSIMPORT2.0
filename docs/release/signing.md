# Code-Signatur

Unsignierte Programme zeigen beim Kunden SmartScreen-Warnungen und werden von manchen
Virenscannern blockiert. Vor der ersten Auslieferung an Kunden ist die Signatur Pflicht
(RELEASE_CHECKLIST.md).

## Was signiert wird

1. `AuftragsImport.exe`, **bevor** sie ins MSI verpackt wird (sonst enthält das MSI die unsignierte Datei).
2. Das fertige MSI.

Signaturen immer mit SHA-256 und RFC-3161-Zeitstempel: Nur so bleiben sie nach Ablauf des
Zertifikats gültig.

## Rahmenbedingungen (Stand 2026)

- Private Schlüssel müssen seit Juni 2023 in Hardware liegen (USB-Token, HSM oder Cloud-Dienst der CA).
  Eine `.pfx`-Datei auf dem Build-Rechner ist nicht mehr möglich.
- Seit 1. März 2026 sind neu ausgestellte Code-Signing-Zertifikate höchstens 460 Tage gültig
  (CA/B Forum, Ballot CSC-31): etwa jährliche Erneuerung einplanen.
- SmartScreen-Reputation: Mehrere Quellen berichten, dass EV-Zertifikate seit März 2024 keinen
  sofortigen Vertrauensvorschuss mehr erhalten; einzelne Anbieter werben weiterhin damit. Ruf entsteht
  über Downloads mit demselben Zertifikat. Immer mit demselben Zertifikat bzw. derselben Identität signieren.

## Option A: Azure Artifact Signing (früher Trusted Signing)

Cloud-Dienst von Microsoft, allgemein verfügbar für Organisationen in EU, Vereinigtem Königreich,
USA und Kanada. Kein Token, direkte Anbindung an GitHub Actions über
`azure/trusted-signing-action` (bereits in der Pipeline vorbereitet). Vorab klären: Das Zertifikat
läuft auf den validierten Namen der juristischen Person. Ob IC-Ware als GbR validiert werden kann
oder dafür eine Eintragung (eGbR) nötig ist, prüft Microsoft bei der Identitätsvalidierung.

Einrichtung:
1. Azure-Abonnement, Artifact-Signing-Konto und Zertifikatsprofil „Public Trust“ anlegen,
   Identitätsvalidierung durchlaufen.
2. App-Registrierung mit föderierter Anmeldung (OIDC) für das GitHub-Repository; Rolle
   „Artifact Signing Certificate Profile Signer“ nur für dieses Profil.
3. In GitHub: Secrets `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`; Variablen `ARTIFACT_SIGNING_ENDPOINT`,
   `ARTIFACT_SIGNING_ACCOUNT`, `ARTIFACT_SIGNING_PROFILE`, `SIGNING_ENABLED=true`.

## Option B: OV-Zertifikat einer Zertifizierungsstelle

Mit Cloud-Signatur der CA (etwa SSL.com eSigner, Certum SimplySign) für die Pipeline, oder mit
USB-Token für lokale Signatur. Lokal über die Vorlage `ICW_SIGN_COMMAND`:

    set ICW_SIGN_COMMAND=signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /sha1 <Fingerabdruck> {file}
    set ICW_VERIFY_COMMAND=signtool verify /pa /all {file}
    python tools/release/build.py all --release --require-signature --build-number 58

Für Artifact Signing lokal: SignTool aus den „Artifact Signing Client Tools“ mit
`/dlib "...\Azure.CodeSigning.Dlib.dll" /dmdf metadata.json` und Zeitstempel
`http://timestamp.acs.microsoft.com`.

## Prüfen

    signtool verify /pa /all /v IC-Ware-AuftragsImport-2.0.1-x64.msi
    powershell Get-AuthenticodeSignature .\AuftragsImport.exe | Format-List

Erwartet: Status „Valid“, Herausgeber IC-Ware, Zeitstempel vorhanden.

## Betrieb

- Zertifikatsablauf im Kalender, 30 Tage vorher erneuern; die Pipeline bleibt unverändert.
- Fehlalarme von Virenscannern: signierte Datei bei Microsoft (WDSI) und dem jeweiligen Hersteller
  zur Prüfung einreichen. onedir ohne UPX und mit Signatur senkt die Rate deutlich.
- Signier-Identität nur für den Release-Workflow freigeben, nie für Pull-Request-Builds.
