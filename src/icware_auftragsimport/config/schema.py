"""Einstellungen: streng typisiert, vollständig validiert, ohne Geheimnisse.

``parse_settings`` sammelt alle Probleme auf einmal, damit ein Administrator eine
fehlerhafte Datei in einem Durchgang korrigieren kann.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, TypeVar

from ..domain.countries import is_country_code
from ..domain.errors import ConfigError, UserMessage
from ..domain.models import Address, PaymentMethod

SCHEMA_VERSION = 1
MAX_PORT = 65535
MAX_FETCH_PER_RUN = 1000
DEFAULT_FETCH_PER_RUN = 200
MAX_RETENTION_DAYS = 3650
DEFAULT_RETENTION_DAYS = 365
MAX_TEXT_LENGTH = 255
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
EXPORT_ADAPTERS = ("lexware_opentrans",)
EXPORT_ENCODINGS = ("UTF-8", "ISO-8859-1")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
SECRET_KEYS = frozenset(
    {
        "password",
        "passwort",
        "kennwort",
        "token",
        "secret",
        "api_key",
        "apikey",
        "client_secret",
        "access_token",
        "refresh_token",
    }
)
ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
PREFIX_PATTERN = re.compile(r"[A-Z0-9]{1,8}")
SENDER_PATTERN = re.compile(r"(?:[A-Za-z0-9._%+-]+)?@?[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")

E = TypeVar("E", bound=StrEnum)


class TlsMode(StrEnum):
    """Art der Verschlüsselung; unverschlüsselt gibt es nicht."""

    IMPLICIT = "implicit"
    STARTTLS = "starttls"


class CredentialSource(StrEnum):
    """Woher das Passwort kommt; Umgebungsvariable nur nach bewusster Wahl."""

    CREDENTIAL_MANAGER = "credential_manager"
    ENVIRONMENT = "environment"


PLAIN_IMAP_PORT = 143
IMPLICIT_TLS_PORT = 993


class AuthMethod(StrEnum):
    """Anmeldeverfahren am Mailserver."""

    PASSWORD = "password"
    OAUTH2 = "oauth2"


class PriceMode(StrEnum):
    """Ob Preise im Export netto oder brutto übergeben werden."""

    NET = "net"
    GROSS = "gross"


@dataclass(frozen=True, slots=True)
class MailAccount:
    """Ein Postfach. Verbindungen sind immer TLS-verschlüsselt."""

    id: str
    name: str
    host: str = ""
    port: int = 993
    username: str = ""
    folder: str = "INBOX"
    archive_folder: str = "Auftrags-Import/Verarbeitet"
    auth_method: AuthMethod = AuthMethod.PASSWORD
    tls_mode: TlsMode = TlsMode.IMPLICIT
    credential_source: CredentialSource = CredentialSource.CREDENTIAL_MANAGER
    allowed_senders: tuple[str, ...] = ()
    enabled: bool = True
    max_messages_per_run: int = DEFAULT_FETCH_PER_RUN


class SenderAction(StrEnum):
    """Was mit Mails eines Absenders geschieht."""

    ACCEPT = "accept"
    REVIEW = "review"
    IGNORE = "ignore"

    @property
    def label(self) -> str:
        """Deutsche Bezeichnung."""
        return _SENDER_ACTION_LABELS[self]


_SENDER_ACTION_LABELS = {
    SenderAction.ACCEPT: "Als Bestellung verarbeiten",
    SenderAction.REVIEW: "Verarbeiten, immer prüfen",
    SenderAction.IGNORE: "Ignorieren",
}
SENDER_RULE_PATTERN = re.compile(r"(?:[a-z0-9._%+-]+)?@[a-z0-9-]+(?:\.[a-z0-9-]+)+")
TEMPLATE_PLACEHOLDERS: dict[str, str] = {
    "firma": "Firma des Kunden",
    "ansprechpartner": "Ansprechpartner des Kunden",
    "bestellnummer": "Bestellnummer des Kunden",
    "belegnummer": "eigene Belegnummer",
    "datum": "Bestelldatum",
    "positionen": "Positionsliste",
    "lieferant": "eigener Firmenname",
}
PLACEHOLDER = re.compile(r"\{([A-Za-z_]+)\}")
DEFAULT_TAX_RATES = (Decimal(0), Decimal(7), Decimal(19))
MAX_TEMPLATE_CHARS = 10_000


@dataclass(frozen=True, slots=True)
class SenderRule:
    """Absenderregel: genaue Adresse (``einkauf@kunde.de``) oder Domain (``@kunde.de``)."""

    pattern: str
    action: SenderAction = SenderAction.ACCEPT


@dataclass(frozen=True, slots=True)
class ShippingOption:
    """Versandart des Profils mit Begriffen, an denen sie in Mails erkannt wird."""

    name: str
    aliases: tuple[str, ...] = ()
    fee: Decimal | None = None


@dataclass(frozen=True, slots=True)
class MailTemplate:
    """Vorlage für die Auftragsbestätigung an den Kunden."""

    subject: str = "Ihre Bestellung {bestellnummer}"
    body: str = (
        "Guten Tag {ansprechpartner},\n\nvielen Dank für Ihre Bestellung {bestellnummer} "
        "vom {datum}. "
        "Wir haben sie unter der Belegnummer {belegnummer} erfasst:\n\n{positionen}\n\n"
        "Mit freundlichen Grüßen\n{lieferant}"
    )


@dataclass(frozen=True, slots=True)
class ImportProfile:
    """Exportziel mit Lieferantendaten und Nummernkreis."""

    id: str
    name: str
    adapter: str = EXPORT_ADAPTERS[0]
    export_dir: str = ""
    test_export_dir: str = ""
    document_prefix: str = "AI"
    price_mode: PriceMode = PriceMode.NET
    default_payment_method: PaymentMethod | None = None
    supplier: Address = field(default_factory=Address)
    export_encoding: str = EXPORT_ENCODINGS[0]
    target_system: str = ""
    target_validated_on: str = ""
    export_dir_network_allowed: bool = False
    mail_account_id: str = ""
    sender_rules: tuple[SenderRule, ...] = ()
    sender_default: SenderAction = SenderAction.ACCEPT
    mail_template: MailTemplate = field(default_factory=MailTemplate)
    shipping_methods: tuple[ShippingOption, ...] = ()
    payment_methods: tuple[PaymentMethod, ...] = tuple(PaymentMethod)
    tax_rates: tuple[Decimal, ...] = DEFAULT_TAX_RATES

    @property
    def company_name(self) -> str:
        """Firmenname laut Lieferantenadresse, sonst Profilname."""
        return self.supplier.company or self.name

    @property
    def target_validated(self) -> bool:
        """True, wenn ein Administrator die Zielsystemvalidierung bestätigt hat."""
        return bool(self.target_system and self.target_validated_on)


@dataclass(frozen=True, slots=True)
class GeneralSettings:
    """Anwendungsweite Einstellungen."""

    log_level: str = "INFO"
    test_mode: bool = False
    dry_run: bool = False
    allow_env_secrets: bool = False
    raw_mail_retention_days: int = DEFAULT_RETENTION_DAYS


DEFAULT_PROFILE = ImportProfile(id="standard", name="Standard")


@dataclass(frozen=True, slots=True)
class Settings:
    """Gesamte Konfiguration."""

    general: GeneralSettings = field(default_factory=GeneralSettings)
    accounts: tuple[MailAccount, ...] = ()
    profiles: tuple[ImportProfile, ...] = (DEFAULT_PROFILE,)
    active_profile_id: str = DEFAULT_PROFILE.id
    schema_version: int = SCHEMA_VERSION

    def profile(self, profile_id: str | None = None) -> ImportProfile:
        """Profil nach ID, ohne Angabe das aktive Profil."""
        wanted = profile_id or self.active_profile_id
        for profile in self.profiles:
            if profile.id == wanted:
                return profile
        raise KeyError(wanted)

    def account(self, account_id: str) -> MailAccount:
        """Mailkonto nach ID."""
        for account in self.accounts:
            if account.id == account_id:
                return account
        raise KeyError(account_id)


class _Reader:
    """Liest ein JSON-Objekt und sammelt Probleme mit Pfadangabe."""

    def __init__(self, data: Mapping[str, Any], path: str, issues: list[str]) -> None:
        self.data = data
        self.path = path
        self.issues = issues

    def problem(self, key: str, text: str) -> None:
        self.issues.append(f"{self.path}.{key}: {text}" if self.path else f"{key}: {text}")

    def reject_unknown(self, allowed: tuple[str, ...]) -> None:
        for key in self.data:
            if key not in allowed and key.casefold() not in SECRET_KEYS:
                self.problem(key, "unbekannter Eintrag")

    def text(self, key: str, default: str = "", *, required: bool = False) -> str:
        raw = self.data.get(key, default)
        if not isinstance(raw, str):
            self.problem(key, "muss ein Text sein")
            return default
        value = raw.strip()
        if required and not value:
            self.problem(key, "darf nicht leer sein")
        if len(value) > MAX_TEXT_LENGTH:
            self.problem(key, f"ist länger als {MAX_TEXT_LENGTH} Zeichen")
        return value

    def pattern(self, key: str, default: str, regex: re.Pattern[str], hint: str) -> str:
        value = self.text(key, default, required=True)
        if value and not regex.fullmatch(value):
            self.problem(key, hint)
        return value

    def integer(self, key: str, default: int, low: int, high: int) -> int:
        value = self.data.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int):
            self.problem(key, "muss eine ganze Zahl sein")
            return default
        if not low <= value <= high:
            self.problem(key, f"muss zwischen {low} und {high} liegen")
        return value

    def boolean(self, key: str, default: bool) -> bool:
        value = self.data.get(key, default)
        if not isinstance(value, bool):
            self.problem(key, "muss true oder false sein")
            return default
        return value

    def choice(self, key: str, default: E, enum_type: type[E]) -> E:
        value = self.data.get(key, default.value)
        allowed = ", ".join(member.value for member in enum_type)
        if isinstance(value, str):
            for member in enum_type:
                if member.value == value:
                    return member
        self.problem(key, f"muss einer dieser Werte sein: {allowed}")
        return default

    def text_list(self, key: str) -> tuple[str, ...]:
        value = self.data.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            self.problem(key, "muss eine Liste von Texten sein")
            return ()
        return tuple(item.strip() for item in value if item.strip())

    def choice_list(self, key: str, enum_type: type[E]) -> list[E]:
        """Liste von Enum-Werten; unbekannte Werte werden gemeldet."""
        values: list[E] = []
        for raw in self.text_list(key):
            try:
                value = enum_type(raw)
            except ValueError:
                self.problem(key, f"„{raw}“ ist unbekannt")
                continue
            if value not in values:
                values.append(value)
        return values

    def child(self, key: str) -> _Reader | None:
        value = self.data.get(key, {})
        if not isinstance(value, dict):
            self.problem(key, "muss ein Objekt sein")
            return None
        return _Reader(value, f"{self.path}.{key}" if self.path else key, self.issues)


def _walk_keys(value: object, path: str) -> Iterator[tuple[str, str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            yield child_path, str(key)
            yield from _walk_keys(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_keys(child, f"{path}[{index}]")


def _secret_issues(data: object) -> list[str]:
    return [
        f"{path}: Geheimnisse gehören nicht in die Einstellungsdatei; "
        "Passwörter liegen in der Windows-Anmeldeinformationsverwaltung. "
        "Bitte den Eintrag entfernen"
        for path, key in _walk_keys(data, "")
        if key.casefold() in SECRET_KEYS
    ]


def _parse_address(reader: _Reader) -> Address:
    reader.reject_unknown(tuple(Address.__dataclass_fields__))
    country = reader.text("country", "DE").upper()
    if country and not is_country_code(country):
        reader.problem("country", "muss ein Ländercode wie DE oder AT sein")
    return Address(
        company=reader.text("company"),
        department=reader.text("department"),
        name=reader.text("name"),
        street=reader.text("street"),
        house_number=reader.text("house_number"),
        postal_code=reader.text("postal_code"),
        city=reader.text("city"),
        country=country,
        phone=reader.text("phone"),
        email=reader.text("email"),
    )


def _parse_account(reader: _Reader) -> MailAccount:
    reader.reject_unknown(tuple(MailAccount.__dataclass_fields__))
    enabled = reader.boolean("enabled", True)
    senders = reader.text_list("allowed_senders")
    for sender in senders:
        if not SENDER_PATTERN.fullmatch(sender):
            reader.problem("allowed_senders", f"„{sender}“ ist keine Mailadresse oder Domain")
    port = reader.integer("port", IMPLICIT_TLS_PORT, 1, MAX_PORT)
    tls_mode = reader.choice("tls_mode", TlsMode.IMPLICIT, TlsMode)
    if tls_mode is TlsMode.IMPLICIT and port == PLAIN_IMAP_PORT:
        reader.problem("port", "Port 143 ist für STARTTLS; implizites TLS nutzt 993")
    if tls_mode is TlsMode.STARTTLS and port == IMPLICIT_TLS_PORT:
        reader.problem("tls_mode", "Port 993 nutzt implizites TLS, nicht STARTTLS")
    return MailAccount(
        id=reader.pattern("id", "", ID_PATTERN, "nur Kleinbuchstaben, Ziffern und Bindestrich"),
        name=reader.text("name", required=True),
        host=reader.text("host", required=enabled),
        port=port,
        username=reader.text("username", required=enabled),
        folder=reader.text("folder", "INBOX", required=True),
        archive_folder=reader.text("archive_folder", "Auftrags-Import/Verarbeitet", required=True),
        auth_method=reader.choice("auth_method", AuthMethod.PASSWORD, AuthMethod),
        tls_mode=tls_mode,
        credential_source=reader.choice(
            "credential_source", CredentialSource.CREDENTIAL_MANAGER, CredentialSource
        ),
        allowed_senders=senders,
        enabled=enabled,
        max_messages_per_run=reader.integer(
            "max_messages_per_run", DEFAULT_FETCH_PER_RUN, 1, MAX_FETCH_PER_RUN
        ),
    )


def _parse_profile(reader: _Reader) -> ImportProfile:
    reader.reject_unknown(tuple(ImportProfile.__dataclass_fields__))
    adapter = reader.text("adapter", EXPORT_ADAPTERS[0], required=True)
    if adapter and adapter not in EXPORT_ADAPTERS:
        reader.problem("adapter", f"unbekannt; verfügbar: {', '.join(EXPORT_ADAPTERS)}")
    payments = tuple(reader.choice_list("payment_methods", PaymentMethod)) or tuple(PaymentMethod)
    payment_raw = reader.data.get("default_payment_method")
    payment: PaymentMethod | None = None
    if payment_raw is not None:
        payment = reader.choice("default_payment_method", PaymentMethod.INVOICE, PaymentMethod)
    supplier_reader = reader.child("supplier")
    supplier = _parse_address(supplier_reader) if supplier_reader else Address()
    export_dir = reader.text("export_dir")
    test_dir = reader.text("test_export_dir")
    encoding = reader.text("export_encoding", EXPORT_ENCODINGS[0]).upper()
    if encoding not in EXPORT_ENCODINGS:
        reader.problem(
            "export_encoding", f"muss einer dieser Werte sein: {', '.join(EXPORT_ENCODINGS)}"
        )
    target_system = reader.text("target_system")
    validated_on = reader.text("target_validated_on")
    if validated_on and not ISO_DATE.fullmatch(validated_on):
        reader.problem("target_validated_on", "muss ein Datum im Format JJJJ-MM-TT sein")
    if bool(target_system) != bool(validated_on):
        reader.problem(
            "target_validated_on",
            "Zielsystem und Validierungsdatum müssen gemeinsam gesetzt oder gemeinsam leer sein",
        )
    if export_dir and test_dir and export_dir.casefold() == test_dir.casefold():
        reader.problem("test_export_dir", "darf nicht gleich dem Exportordner sein")
    return ImportProfile(
        id=reader.pattern("id", "", ID_PATTERN, "nur Kleinbuchstaben, Ziffern und Bindestrich"),
        name=reader.text("name", required=True),
        adapter=adapter,
        export_dir=export_dir,
        test_export_dir=test_dir,
        document_prefix=reader.pattern(
            "document_prefix", "AI", PREFIX_PATTERN, "1 bis 8 Großbuchstaben oder Ziffern"
        ),
        price_mode=reader.choice("price_mode", PriceMode.NET, PriceMode),
        default_payment_method=payment,
        supplier=supplier,
        export_encoding=encoding if encoding in EXPORT_ENCODINGS else EXPORT_ENCODINGS[0],
        target_system=target_system,
        target_validated_on=validated_on,
        export_dir_network_allowed=reader.boolean("export_dir_network_allowed", False),
        mail_account_id=reader.text("mail_account_id"),
        sender_rules=_parse_sender_rules(reader),
        sender_default=reader.choice("sender_default", SenderAction.ACCEPT, SenderAction),
        mail_template=_parse_template(reader),
        shipping_methods=_parse_shipping(reader),
        payment_methods=payments,
        tax_rates=_parse_tax_rates(reader),
    )


def _objects(reader: _Reader, key: str, allowed: tuple[str, ...]) -> list[_Reader]:
    raw = reader.data.get(key, [])
    if not isinstance(raw, list):
        reader.problem(key, "muss eine Liste sein")
        return []
    items = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            reader.problem(f"{key}[{index}]", "muss ein Objekt sein")
            continue
        child = _Reader(item, f"{reader.path}.{key}[{index}]", reader.issues)
        child.reject_unknown(allowed)
        items.append(child)
    return items


def _decimal_value(reader: _Reader, key: str, low: Decimal, high: Decimal) -> Decimal | None:
    raw = reader.data.get(key)
    if raw is None or raw == "":
        return None
    try:
        value = Decimal(str(raw).replace(",", "."))
    except InvalidOperation:
        reader.problem(key, f"„{raw}“ ist keine Zahl")
        return None
    if not value.is_finite() or not low <= value <= high:
        reader.problem(key, f"muss zwischen {low} und {high} liegen")
        return None
    return value


def _parse_sender_rules(reader: _Reader) -> tuple[SenderRule, ...]:
    rules: list[SenderRule] = []
    for child in _objects(reader, "sender_rules", ("pattern", "action")):
        pattern = child.text("pattern", required=True).strip().lower()
        if pattern and not SENDER_RULE_PATTERN.fullmatch(pattern):
            child.problem("pattern", "Mailadresse (name@firma.de) oder Domain (@firma.de) erwartet")
        if pattern in {r.pattern for r in rules}:
            child.problem("pattern", f"„{pattern}“ ist doppelt")
        rules.append(SenderRule(pattern, child.choice("action", SenderAction.ACCEPT, SenderAction)))
    return tuple(rules)


def _parse_template(reader: _Reader) -> MailTemplate:
    child = reader.child("mail_template")
    if child is None:
        return MailTemplate()
    child.reject_unknown(("subject", "body"))
    subject = child.text("subject", MailTemplate().subject)
    body = child.text("body", MailTemplate().body)
    for key, text in (("subject", subject), ("body", body)):
        if len(text) > MAX_TEMPLATE_CHARS:
            child.problem(key, f"höchstens {MAX_TEMPLATE_CHARS} Zeichen")
        unknown = sorted({p for p in PLACEHOLDER.findall(text) if p not in TEMPLATE_PLACEHOLDERS})
        if unknown:
            child.problem(
                key, f"unbekannte Platzhalter: {', '.join('{' + u + '}' for u in unknown)}"
            )
    return MailTemplate(subject, body)


def _parse_shipping(reader: _Reader) -> tuple[ShippingOption, ...]:
    options: list[ShippingOption] = []
    for child in _objects(reader, "shipping_methods", ("name", "aliases", "fee")):
        name = child.text("name", required=True)
        if name.casefold() in {o.name.casefold() for o in options}:
            child.problem("name", f"„{name}“ ist doppelt")
        aliases = tuple(a for a in child.text_list("aliases") if a.strip())
        fee = _decimal_value(child, "fee", Decimal(0), Decimal(100_000))
        options.append(ShippingOption(name, aliases, fee))
    return tuple(options)


def _parse_tax_rates(reader: _Reader) -> tuple[Decimal, ...]:
    raw = reader.data.get("tax_rates")
    if raw is None:
        return DEFAULT_TAX_RATES
    if not isinstance(raw, list) or not raw:
        reader.problem("tax_rates", "muss eine nicht leere Liste sein")
        return DEFAULT_TAX_RATES
    rates: list[Decimal] = []
    for index, value in enumerate(raw):
        rate = _decimal_value(
            _Reader({"v": value}, f"{reader.path}.tax_rates[{index}]", reader.issues),
            "v",
            Decimal(0),
            Decimal(100),
        )
        if rate is not None and rate not in rates:
            rates.append(
                rate.normalize() if rate != rate.to_integral_value() else Decimal(int(rate))
            )
    return tuple(sorted(rates)) or DEFAULT_TAX_RATES


def _cross_profile_issues(
    profiles: tuple[ImportProfile, ...], accounts: tuple[MailAccount, ...]
) -> list[str]:
    """Konflikte zwischen Profilen: gemeinsame Exportordner oder Postfächer vermischen Firmen."""
    issues: list[str] = []
    owners: dict[str, str] = {}
    for profile in profiles:
        for label, path in (
            ("Exportordner", profile.export_dir),
            ("Testordner", profile.test_export_dir),
        ):
            if not path:
                continue
            key = _path_key(path)
            if key in owners and owners[key] != profile.id:
                issues.append(
                    f"profiles.{profile.id}: {label} wird schon vom Profil „{owners[key]}“ benutzt"
                )
            owners.setdefault(key, profile.id)
    account_ids = {a.id for a in accounts}
    used: dict[str, str] = {}
    for profile in profiles:
        account = profile.mail_account_id
        if not account:
            continue
        if account not in account_ids:
            issues.append(
                f"profiles.{profile.id}.mail_account_id: Postfach „{account}“ existiert nicht"
            )
        elif account in used:
            issues.append(
                f"profiles.{profile.id}.mail_account_id: Postfach „{account}“ "
                f"gehört bereits zu „{used[account]}“"
            )
        used.setdefault(account, profile.id)
    return issues


def _path_key(path: str) -> str:
    return path.replace("\\", "/").rstrip("/").casefold()


def _parse_general(reader: _Reader) -> GeneralSettings:
    reader.reject_unknown(tuple(GeneralSettings.__dataclass_fields__))
    level = reader.text("log_level", "INFO").upper()
    if level not in LOG_LEVELS:
        reader.problem("log_level", f"muss einer dieser Werte sein: {', '.join(LOG_LEVELS)}")
        level = "INFO"
    return GeneralSettings(
        log_level=level,
        test_mode=reader.boolean("test_mode", False),
        dry_run=reader.boolean("dry_run", False),
        allow_env_secrets=reader.boolean("allow_env_secrets", False),
        raw_mail_retention_days=reader.integer(
            "raw_mail_retention_days", DEFAULT_RETENTION_DAYS, 1, MAX_RETENTION_DAYS
        ),
    )


def _items(root: _Reader, key: str) -> list[_Reader]:
    value = root.data.get(key, [])
    if not isinstance(value, list):
        root.problem(key, "muss eine Liste sein")
        return []
    readers = []
    for index, item in enumerate(value):
        if isinstance(item, dict):
            readers.append(_Reader(item, f"{key}[{index}]", root.issues))
        else:
            root.problem(f"{key}[{index}]", "muss ein Objekt sein")
    return readers


def _duplicates(ids: list[str]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for item in ids:
        if item in seen:
            duplicates.add(item)
        seen.add(item)
    return duplicates


def parse_settings(data: object) -> Settings:
    """Liest und prüft Einstellungen; wirft ``ConfigError`` mit allen Problemen."""
    if not isinstance(data, dict):
        raise _config_error(["Die Datei muss ein JSON-Objekt enthalten"])
    issues = _secret_issues(data)
    root = _Reader(data, "", issues)
    root.reject_unknown(("schema_version", "general", "accounts", "profiles", "active_profile_id"))
    version = root.integer("schema_version", SCHEMA_VERSION, SCHEMA_VERSION, SCHEMA_VERSION)
    general_reader = root.child("general")
    general = _parse_general(general_reader) if general_reader else GeneralSettings()
    accounts = tuple(_parse_account(r) for r in _items(root, "accounts"))
    profiles = tuple(_parse_profile(r) for r in _items(root, "profiles")) or (DEFAULT_PROFILE,)
    for dup in sorted(_duplicates([a.id for a in accounts])):
        issues.append(f"accounts: die ID „{dup}“ kommt mehrfach vor")
    for dup in sorted(_duplicates([p.id for p in profiles])):
        issues.append(f"profiles: die ID „{dup}“ kommt mehrfach vor")
    active = root.text("active_profile_id", profiles[0].id, required=True)
    if active and active not in {p.id for p in profiles}:
        root.problem("active_profile_id", f"verweist auf kein vorhandenes Profil („{active}“)")
    issues += _cross_profile_issues(profiles, accounts)
    for profile in profiles:
        if (
            profile.default_payment_method
            and profile.default_payment_method not in profile.payment_methods
        ):
            issues.append(
                f"profiles.{profile.id}: Standard-Zahlungsart ist im Profil nicht freigegeben"
            )
    if issues:
        raise _config_error(issues)
    return Settings(
        general=general,
        accounts=accounts,
        profiles=profiles,
        active_profile_id=active,
        schema_version=version,
    )


def _config_error(issues: list[str]) -> ConfigError:
    count = len(issues)
    noun = "Problem" if count == 1 else "Probleme"
    return ConfigError(
        "CONFIG_INVALID",
        UserMessage(
            what="Die Einstellungen konnten nicht übernommen werden",
            why=f"Es wurden {count} {noun} gefunden: " + "; ".join(issues),
            unchanged="Die bisher gültigen Einstellungen bleiben unverändert",
            action="Bitte die genannten Einträge korrigieren und erneut laden",
        ),
        details=tuple(issues),
    )


def settings_to_dict(settings: Settings) -> dict[str, Any]:
    """Serialisiert Einstellungen in fester Reihenfolge."""

    def address(value: Address) -> dict[str, str]:
        return dict(value.values())

    return {
        "schema_version": settings.schema_version,
        "general": {
            "log_level": settings.general.log_level,
            "test_mode": settings.general.test_mode,
            "dry_run": settings.general.dry_run,
            "allow_env_secrets": settings.general.allow_env_secrets,
            "raw_mail_retention_days": settings.general.raw_mail_retention_days,
        },
        "accounts": [
            {
                "id": a.id,
                "name": a.name,
                "host": a.host,
                "port": a.port,
                "username": a.username,
                "folder": a.folder,
                "archive_folder": a.archive_folder,
                "auth_method": a.auth_method.value,
                "tls_mode": a.tls_mode.value,
                "credential_source": a.credential_source.value,
                "allowed_senders": list(a.allowed_senders),
                "enabled": a.enabled,
                "max_messages_per_run": a.max_messages_per_run,
            }
            for a in settings.accounts
        ],
        "profiles": [
            {
                "id": p.id,
                "name": p.name,
                "adapter": p.adapter,
                "export_dir": p.export_dir,
                "test_export_dir": p.test_export_dir,
                "document_prefix": p.document_prefix,
                "price_mode": p.price_mode.value,
                "default_payment_method": (
                    p.default_payment_method.value if p.default_payment_method else None
                ),
                "supplier": address(p.supplier),
                "export_encoding": p.export_encoding,
                "target_system": p.target_system,
                "target_validated_on": p.target_validated_on,
                "export_dir_network_allowed": p.export_dir_network_allowed,
                "mail_account_id": p.mail_account_id,
                "sender_rules": [
                    {"pattern": r.pattern, "action": r.action.value} for r in p.sender_rules
                ],
                "sender_default": p.sender_default.value,
                "mail_template": {"subject": p.mail_template.subject, "body": p.mail_template.body},
                "shipping_methods": [
                    {
                        "name": o.name,
                        "aliases": list(o.aliases),
                        "fee": None if o.fee is None else str(o.fee),
                    }
                    for o in p.shipping_methods
                ],
                "payment_methods": [m.value for m in p.payment_methods],
                "tax_rates": [str(r) for r in p.tax_rates],
            }
            for p in settings.profiles
        ],
        "active_profile_id": settings.active_profile_id,
    }
