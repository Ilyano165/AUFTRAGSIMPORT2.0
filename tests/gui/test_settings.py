"""Einstellungen → Firmenprofile und → Artikelkatalog, Profilwechsel, Zuordnungsdetails."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from icware_auftragsimport.app.demo import NORDLICHT
from icware_auftragsimport.app.presentation import DetailTab
from icware_auftragsimport.app.workbench import Workbench
from icware_auftragsimport.domain.models import MatchStatus
from icware_auftragsimport.domain.status import OrderStatus
from icware_auftragsimport.gui.main_window import MainWindow
from icware_auftragsimport.gui.settings.catalog_dialogs import (
    BackupDialog,
    ImportDialog,
    RematchDialog,
    VersionsDialog,
)
from icware_auftragsimport.gui.settings.dialog import SettingsDialog

PRICE_LIST = (
    "Artikelnummer;Bezeichnung;Einheit;Steuerart;Verkaufspreis netto;GTIN;Aktiv\r\n"
    "MW-0710;Mineralwasser still 12 × 0,7 l;Kiste;19 %;7,20;4012345000017;ja\r\n"
    "HM-1000;Bio-Hafermilch 1 l;Karton;7 %;2,10;;ja\r\n"
).encode("cp1252")


def test_settings_dialog_has_profiles_accounts_and_catalog(
    qapp: QApplication, workbench: Workbench
) -> None:
    dialog = SettingsDialog(workbench)
    assert [dialog.nav.item(i).text() for i in range(dialog.nav.count())] == [
        "Firmenprofile",
        "Postfächer",
        "Artikelkatalog",
    ]
    assert dialog.profiles.list.count() == 2
    dialog.open_catalog()
    assert dialog.stack.currentWidget() is dialog.catalog
    assert dialog.catalog.model.rowCount() == 8
    labels = [b.text() for b in dialog.catalog.buttons.values()]
    assert labels == [
        "Importieren …",
        "Exportieren …",
        "Validieren",
        "Neu zuordnen …",
        "Backup …",
        "Versionen …",
    ]
    dialog.close()


def test_profile_edit_saves_and_conflicts_are_explained(
    qapp: QApplication, workbench: Workbench
) -> None:
    dialog = SettingsDialog(workbench)
    page = dialog.profiles
    QTest.keyClicks(page.name, " GmbH")
    assert page.dirty
    assert page.save()
    assert workbench.profile.name == "Muster Getränke GmbH"
    nordlicht_dir = workbench.settings.profile(NORDLICHT).export_dir
    page.export_dir.setText(nordlicht_dir)
    page._changed()
    assert not page.save()
    assert "Nicht gespeichert" in page.notice.text()
    assert "Nordlicht" in page.notice.text() or "nordlicht" in page.notice.text()
    assert workbench.profile.export_dir != nordlicht_dir
    page._set_dirty(False)


def test_new_profile_and_sender_rule_test(qapp: QApplication, workbench: Workbench) -> None:
    dialog = SettingsDialog(workbench)
    page = dialog.profiles
    page.new_profile()
    page.name.setText("Firma C")
    page.profile_id.setText("firma-c")
    page.prefix.setText("FC")
    assert page.save()
    assert [p.id for p in workbench.profiles][-1] == "firma-c"
    page.reload(select="standard")
    page.sender_test.setText("einkauf@nord.gasthaus.example")
    assert "Als Bestellung verarbeiten" in page.sender_result.text()
    assert "@gasthaus.example" in page.sender_result.text()


def test_import_wizard_maps_checks_and_imports(qapp: QApplication, workbench: Workbench) -> None:
    wizard = ImportDialog(workbench)
    wizard.load_bytes(PRICE_LIST, "preisliste.csv")
    assert wizard.table is not None
    assert wizard.mapping().missing() == []
    wizard.go(1)
    assert wizard.mapped.rowCount() == 2
    wizard.go(2)
    assert wizard.preview is not None
    assert wizard.import_button.isEnabled()
    assert "Backup" in wizard.check_notice.text()
    wizard.run_import()
    assert wizard.stack.currentIndex() == 3
    assert wizard.result_title.text() == "Version 3 ist aktiv"
    assert workbench.catalog_version() == 3
    assert RematchDialog(workbench).plan.changes


def test_import_with_errors_cannot_be_started(qapp: QApplication, workbench: Workbench) -> None:
    wizard = ImportDialog(workbench)
    wizard.load_bytes(b"Artikelnummer;Bezeichnung;Preis\nX;A;abc\nX;B;1\n", "kaputt.csv")
    wizard.go(2)
    assert not wizard.import_button.isEnabled()
    assert "Import nicht möglich" in wizard.check_notice.text()
    assert workbench.catalog_version() == 2


def test_versions_rollback_and_backup(qapp: QApplication, workbench: Workbench) -> None:
    versions = VersionsDialog(workbench)
    assert [v.number for v in versions.versions] == [2, 1]
    versions.activate_version(versions.versions[1].version)
    assert workbench.catalog_version() == 1
    backups = BackupDialog(workbench)
    before = len(backups.entries)
    backups.create_backup()
    assert len(backups.entries) == before + 1
    preview = workbench.catalogs.restore(workbench.profile, backups.entries[-1].path)
    backups.apply_restore(preview, "test")
    assert workbench.catalog_version() == 3


def test_profile_switch_and_candidate_acceptance(qapp: QApplication, window: MainWindow) -> None:
    combo = window.profile_switch
    assert combo.count() == 2
    combo.setCurrentIndex(combo.findData(NORDLICHT))
    assert window.workbench.profile.id == NORDLICHT
    assert window.table.proxy.rowCount() == 2
    window.table.select_order("order-102")
    window.detail.show_tab(DetailTab.LINES)
    lines = window.detail.lines
    assert lines.table.currentRow() == 1
    panel = lines.match_panel
    assert panel.values["status"].text() == "Benutzerprüfung erforderlich"
    assert panel.values["method"].text() == "ähnlicher Name"
    assert "89 %" in panel.values["certainty"].text()
    assert panel.candidates.count() == 1
    QTest.mouseClick(panel.accept_button, Qt.MouseButton.LeftButton)
    snapshot = window.detail.snapshot
    assert snapshot is not None
    assert snapshot.order.lines[1].match.status is MatchStatus.MANUAL
    assert snapshot.order.status is OrderStatus.READY
