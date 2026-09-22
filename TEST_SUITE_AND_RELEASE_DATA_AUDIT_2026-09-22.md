# ProgTrack: Test-Suite- und Release-Daten-Audit

Stand: 22.09.2026  
Checkout: Phase-0.2.3  
Arbeitsbaum: Bereits vor diesem Audit verändert; nicht zu dieser Aufgabe gehörende Änderungen wurden nicht angefasst.

## Ergebnis in Kürze

Die Testsuite war tatsächlich zu Heritage-Track-lastig und enthielt außerdem eine konkrete Doppelregistrierung von Tests. Die Standardausführung ist jetzt auf fachliche Verträge, Backend, Dialoge, Plugins und schnelle Heritage-Tests ausgerichtet. Teure vollständige Seed-Render- und Pixel-Audits bleiben vollständig erhalten, laufen aber als explizit markierte Erweiterung.

Der optimierte Standardlauf ist nicht grün:

- 607 Tests bestanden
- 5 Tests fehlgeschlagen
- 17 teure Heritage-Testknoten abgewählt
- 188 Subtests bestanden
- Laufzeit: 3:52 Minuten

Der separate erweiterte Heritage-Lauf ist ebenfalls nicht grün:

- 13 Testmethoden bestanden
- 8 Testmethoden bzw. Subtest-Fälle fehlgeschlagen
- 20 Subtests ausgewertet
- Laufzeit: 13:50 Minuten

Es wurden keine Produktionsdateien repariert und keine Daten gelöscht. Die gefundenen Probleme sind unten nach Produktfehler, veralteter Testannahme und Testinfrastruktur getrennt.

## Änderungen an der Testsuite

### Entfernte Doppelabdeckung

tests/test_issue_211_manual_animal_repositioning.py erbte zuvor von HeritagePositionWidgetTest. Dadurch wurden die gesamten geerbten Positionierungs- und Mausinteraktionstests zusätzlich unter dem Issue-211-Modul gesammelt. Das Modul sammelt jetzt nur noch seine drei eigenen Tests und verwendet die gemeinsame Fixture-Klasse erst zur Laufzeit.

Messung:

| Zustand | gesammelte Testknoten | Knoten mit Heritage im Namen |
| --- | ---: | ---: |
| vorher | 693 | 335 |
| nachher | 629 | 271 |

Damit wurden 64 doppelte Testknoten entfernt; die Heritage-Namensquote sank von etwa 48,3 % auf etwa 43,1 %, ohne Fachtests zu löschen.

### Repräsentative statt kartesische Seed-Matrix

Die bisherige vollständige Matrix renderte jede Spezies mit jeder Auswahlform, jeder Tiefe und beiden vertikalen Modi. Die Standardmatrix verwendet jetzt je eine begründete Repräsentation pro Seed-Spezies und prüft weiterhin beide Modi:

- Callitrix jacchus: all, Tiefe 6
- Macaca mulatta: single, Tiefe 3
- Mus musculus: all, Tiefe 3
- Papio hamadryas anubis: single, Tiefe 6

Spezifische Grenzfälle, Mausinteraktion, Callitrix-Fokus, ungleiche Tiefe, Legenden und Pixelabstände bleiben als fokussierte Tests erhalten.

### Erweiterte Heritage-Audits

pytest.ini registriert den Marker extended_heritage und nimmt diese teuren Renderprüfungen standardmäßig aus dem Lauf. Sie werden nicht gelöscht und sind jederzeit reproduzierbar:

    $env:PYTHONPATH=(Get-Location).Path
    pytest -q --tb=short -rA
    pytest -q -m extended_heritage --tb=short -rA

Markiert sind nur die teuren vollständigen Seed-Renderpfade. Der Standardlauf enthält weiterhin die Heritage-Backend-, Router-, Cache-, Berechtigungs-, Auswahl- und Interaktionstests.

## Ausgeführte Tests und Befunde

### Optimierter Standardlauf

Befehl:

    pytest -q --tb=short -rA

Die fünf Fehler sind:

1. tests/test_codehealth_targeted_fixes.py::CodeHealthTargetedFixesTest::test_backend_events_are_canonical_and_report_index_uses_them erwartet Datumswerte aus Testdaten mit dem alten Feld typ; die aktuelle Canonical-Event-Kette arbeitet mit event_type, daher bleibt donor_recovery_dates leer.
2. tests/test_phase2b_animal_dialog_statistics_regressions.py::AnimalDialogStatisticsRegressionTests::test_custom_limits_are_read_from_backend_map_and_stable_event_ids erwartet ein Ereignis mit typ=custom-event-biopsy-v1, erhält aber Biopsy: 0/3 statt 1/3.
3. tests/test_phase2b_animal_dialog_statistics_regressions.py::AnimalDialogStatisticsRegressionTests::test_experimental_offspring_and_sperm_statistics_are_not_steroid_gated verwendet ebenfalls alte typ-Ereignisse; das Ergebnis ist Surgery: 0/3, Measurement: 0/4 statt 1/3 und 1/4.
4. tests/test_phase2b_block3.py::Block3Tests::test_master_sessions_are_isolated_and_guest_does_not_leak_outgoing_style verwendet ein Test-Double Master, das load_session() anbietet, aber save_session() nicht implementiert. Die Produktionsklasse Plugins/Master_Track/plugin.py besitzt diese Methode; dies ist daher ein veralteter Fixture-Vertrag, kein nachgewiesener Produktionsfehler.
5. tests/test_phase2b_current_build_fixes.py::SeedAndIconTest::test_seed_contains_canonical_users_and_complete_reproductive_links vergleicht die alten typ=sperm_donation-Ereignisse mit der sperm-Liste. Dadurch fehlt im Testset SP-OTOF-DENETHOR-001, obwohl die Event-Migration gerade auf event_type umgestellt wird.

Die ersten drei und der fünfte Befund sind daher zunächst Test-/Fixture-Drift im Zuge der Event-Type-Migration und sollten auf event_type umgestellt werden, bevor sie als Produktregression gewertet werden. Der vierte ist ein unvollständiges Test-Double. Die Produktionsänderungen im Arbeitsbaum waren bereits vorhanden; dieser Audit hat sie nicht verändert.

Zusätzliche nicht-fatal beobachtete Umgebungswarnungen:

- Qt meldet die fehlende gebündelte Font-Bibliothek aus der lokalen PyQt-Installation.
- Qt meldet bei einigen Offscreen-Tests propagateSizeHints() nicht unterstützt.
- Eine PDF-Prüfung meldet eine pypdf-Deprecation.
- Die erweiterten Pixeltests erzeugen sehr viele QImage::pixelColor-Bereichswarnungen; das ist Test-/Renderer-Rauschen, muss aber bei einer späteren Pixeltest-Bereinigung begrenzt werden.

### Erweiterter Heritage-Lauf

Befehl:

    pytest -q -m extended_heritage --tb=short -rA

Bestanden haben 13 Fälle, darunter die Arwen-Tiefenfälle, Drag/Pan, deterministische Positionierung, Zoom und mehrere Fokusprüfungen.

Die acht fehlgeschlagenen Fälle lassen sich auf zwei Gruppen reduzieren:

#### A. Reale bzw. reproduzierbare Seed-Geometrieprobleme

Die Callitrix-Layouts werden vom Validator wegen fremder Marker auf einer Elternroute abgelehnt:

- Route zu Beth schneidet den Marker von Elrond.
- Route zu Aredhel schneidet den Marker von Argon.
- Route zu Elurin schneidet den Marker von Elured.

Das tritt in test_denethor_selected_single_children_follow_family_axes, den beiden Callitrix-Pixel-Subtests, den beiden Callitrix-Fällen der repräsentativen Matrix sowie beim Genotyp-Legenden- und Overview-Fall auf. Der Renderer lehnt den Kandidaten bereits vor dem endgültigen Bild ab; das ist kein bloßes Zoom- oder Screenshotproblem. Diese Befunde gehören in die weitere Heritage-Geometrie-/Routingarbeit.

#### B. Reihenfolgeabhängiger Heritage-Test

test_denethor_eldarion_partner_rail_stays_readable meldete im vollständigen erweiterten Lauf eine Marker-/Label-Überlappung. Derselbe Test bestand isoliert erneut:

    pytest -q -o addopts='' tests/test_heritage_track_current_seed_matrix.py::CurrentSeedHeritageMatrixTest::test_denethor_eldarion_partner_rail_stays_readable

Das weist auf gemeinsam genutzten Widget-/Cache-Zustand oder fehlende Fixture-Rücksetzung zwischen erweiterten Tests hin. Es ist deshalb als Testisolationsproblem zu behandeln, bis es in einer isolierten und einer sequenziellen Ausführung gleich reproduziert wird.

## Backups, temporäre und nicht releasefähige Daten

Die folgende Inventur ist read-only. Es wurde nichts verschoben oder gelöscht. Die Klassifizierung folgt dem vorhandenen .gitignore und Depricated.md.

| Pfad | Befund | Einordnung |
| --- | --- | --- |
| ProgTrackData/database/backups/ | 4 SQLite-Sicherungen: vor Event-Schema, vor Event-Type-Payload-Migration (zwei Varianten) und vor Project-History-Reparatur; zusammen ca. 11,8 MB | Wertvolle lokale Wiederherstellungspunkte; nicht in ein Release packen, aber erst nach verifizierter Migration löschen oder extern archivieren. |
| ProgTrackData/ | aktive SQLite-Datenbank, WAL/SHM, Logs, Cache und managed Logo; insgesamt ca. 24,0 MB | lokale Laufzeit-/Benutzerdaten; korrekt ignoriert, nicht als unbenutzt löschen. |
| archive/Heritage_Track_2026-08-11/ | historische Heritage-Pythonstände plus .pyc | lokale immutable Alt-/Rollback-Sicherung; nicht runtime- oder releasefähig. |
| archive/HT (phase II)/ | INVENTAR.md, WIEDERHERSTELLUNG.md und stale Heritage-Bytecode | historische Wiederherstellungsreferenz; separat archivieren, niemals importieren oder paketieren. |
| build/launcher_small/ | 16 PyInstaller-Artefakte, darunter Launcher.exe, .pkg, .pyz, .toc und Warn-/xref-Dateien; ca. 67,9 MB | generiertes Build-Staging; nur der freigegebene Launcher gehört in ein fertiges Paket, Build-Zwischenprodukte nicht. |
| tmp/ | 239 Dateien, vor allem 183 PNGs, 39 JSONs, PDFs, Audit-Skripte, Benchmarks und Profile; ca. 17,8 MB | Test-/Screenshot-/Diagnosematerial, vollständig temporär und durch .gitignore ausgeschlossen. |
| source/.build_env_py312/ und source/.build_env_py313/ | zwei lokale virtuelle Build-Umgebungen | nicht benötigte Release-Daten; für lokale Reproduzierbarkeit behalten oder außerhalb des Release-Ordners verschieben. |
| source/.build_tmp/ | Packaging-Logs | generiertes Zwischenmaterial; nicht releasefähig. |
| source/build/ und source/dist/ | PyInstaller-Build und portable Distribution | generiert; vor einem sauberen Release neu erzeugen, nicht als Sourcebestand behandeln. |
| source/payload/ | altes Payload mit ProgTrack.v.0.1.2.py und Plugins | klar historischer Payload-Kandidat; nicht in v0.2.3/v1.0 übernehmen. |
| source/release/ | Linux-/Staging-Ausgaben sowie ProgTrack-0.2.1.zip, SHA-256-Datei und payload.zip | historische bzw. generierte Release-Ausgaben; vor einem neuen Release gegen die aktuelle Version prüfen und alte Archive außerhalb des Arbeits-Releaseordners archivieren. |
| _internal/logs/Master_Track/ | zwei lokale Audit-Logs aus 2026-08 und 2026-09 im portablen Runtime-Baum | potentiell sensible Laufzeitdaten; vor Veröffentlichung aus dem Bundle entfernen bzw. Packaging-Regel dafür ergänzen. |
| _internal/ insgesamt | ca. 1.977 Dateien, ca. 282 MB | die native portable Windows-Laufzeit ist laut Depricated.md absichtlich Bestandteil des Root-Bundles; nicht pauschal löschen. Nur Logs und zufällige Runtime-Reste prüfen. |
| __pycache__, .pytest_cache, .ruff_cache, *.pyc | laufzeitgenerierte Bytecodes und Testcache | nicht releasefähig; ignoriert und regenerierbar, historische .pyc in archive/ aber als Teil der Rückfallebene behandeln. |
| Depricated.md | lokale, ignorierte Bereinigungs-/Migrationsliste | Dokumentation, kein Runtimebestand. Der Dateiname ist historisch falsch geschrieben, aber bereits als lokale Datei vorgesehen. |
| Username + 123456 password.png | 11 KB, im Root und nicht durch .gitignore ausgeschlossen | Sicherheits-/Release-Risiko: Demo-Zugangsdaten gehören nicht in ein distributables Paket. Nicht automatisch gelöscht, sollte aber vor Veröffentlichung entfernt oder in eine ausdrücklich nicht paketierte Testreferenz verschoben werden. |

### Empfohlene Reihenfolge für spätere Bereinigung

1. Aktive Datenbank und die vier Migrationsbackups sichern und erst nach einem Restore-Test archivieren.
2. Das Demo-Passwortbild aus dem Releaseinhalt entfernen.
3. _internal/logs/ aus dem Packaging ausschließen bzw. vor der Paketbildung leeren, während die benötigten nativen Bibliotheken erhalten bleiben.
4. source/payload/, alte source/release/-Archive sowie Build- und Temp-Ausgaben außerhalb des aktiven Releaseordners archivieren.
5. Vor jedem Release aus sauberem Build- und Seed-Zustand paketieren; keine lokalen ProgTrackData-, tmp- oder Backup-Dateien übernehmen.

Diese Schritte wurden in diesem Audit nicht ausgeführt, weil sie Daten löschen oder verschieben würden.
