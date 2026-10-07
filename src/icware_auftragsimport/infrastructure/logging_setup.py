"""Protokoll mit Rotation; jede Zeile wird vor dem Schreiben bereinigt."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ..branding import LOGGER_NAME
from ..security.redaction import anonymize_paths, redact

LOG_FILE_NAME = "auftrags-import.log"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 10
LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_HANDLER_MARK = "_icware_handler"


MAX_RECORD_CHARS = 4_000


class RedactingFormatter(logging.Formatter):
    """Bereinigt die fertige Zeile inklusive Ausnahmetext und begrenzt ihre Länge.

    Die Begrenzung verhindert, dass versehentlich ganze Mailinhalte im Protokoll landen.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Formatiert, kürzt und entfernt sensible Daten."""
        text = anonymize_paths(redact(super().format(record)[: MAX_RECORD_CHARS * 2]))
        if len(text) > MAX_RECORD_CHARS:
            text = text[:MAX_RECORD_CHARS] + " …[gekürzt]"
        return text


def configure_logging(
    log_dir: Path, level: str = "INFO", *, console: bool = False
) -> logging.Logger:
    """Richtet das Anwendungsprotokoll ein; mehrfacher Aufruf ersetzt die Handler."""
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_MARK, False):
            logger.removeHandler(handler)
            handler.close()
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = RedactingFormatter(LOG_FORMAT)
    file_handler = RotatingFileHandler(
        log_dir / LOG_FILE_NAME,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handlers: list[logging.Handler] = [file_handler]
    if console:
        handlers.append(logging.StreamHandler())
    for handler in handlers:
        handler.setFormatter(formatter)
        setattr(handler, _HANDLER_MARK, True)
        logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger


def get_logger(name: str) -> logging.Logger:
    """Unterlogger, etwa ``icware.store``."""
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
