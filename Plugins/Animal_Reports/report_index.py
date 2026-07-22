"""Fast, revision-aware indexes used by the monthly Reports view."""

from __future__ import annotations

import hashlib
import json
import logging
import pickle
from bisect import bisect_right
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple


logger = logging.getLogger(__name__)

LEGACY_EVENT_KEYS = (
    "op",
    "pgf",
    "embryo",
    "abort",
    "geburt",
    "trächtigkeit",
    "fsh",
    "progesterone",
)

_REVISION_FIELDS = (
    "rolle",
    "role",
    "sex",
    "birth_date",
    "death_date",
    "sterbedatum",
    "sick_start_date",
    "sick_end_date",
    "sick_times",
    "abnormal_start_date",
    "abnormal_end_date",
    "recovery_time",
    "ref_weight",
    "reproduktionsfeld",
    "max_messungen",
    "max_measurements",
    "max_fsh",
    "max_op",
    "max_embryo",
    "max_pregnancies",
    "max_geburten",
    "max_pgf",
    "max_special",
    "daten",
    "pdg",
    "gewicht",
    "sperm",
    "events",
    "edits",
) + LEGACY_EVENT_KEYS


def _hash_value(hasher: "hashlib._Hash", value: Any) -> None:
    """Feed nested report data into *hasher* without building a giant tuple."""
    if value is None:
        hasher.update(b"n;")
    elif isinstance(value, bool):
        hasher.update(b"b1;" if value else b"b0;")
    elif isinstance(value, datetime):
        hasher.update(b"t")
        hasher.update(value.isoformat().encode("utf-8", "surrogatepass"))
        hasher.update(b";")
    elif isinstance(value, date):
        hasher.update(b"d")
        hasher.update(value.isoformat().encode("ascii"))
        hasher.update(b";")
    elif isinstance(value, (str, int, float)):
        hasher.update(type(value).__name__.encode("ascii"))
        hasher.update(b":")
        hasher.update(repr(value).encode("utf-8", "surrogatepass"))
        hasher.update(b";")
    elif isinstance(value, Mapping):
        hasher.update(b"{")
        for key in sorted(value, key=lambda item: str(item)):
            _hash_value(hasher, str(key))
            _hash_value(hasher, value[key])
        hasher.update(b"}")
    elif isinstance(value, (list, tuple)):
        hasher.update(b"[")
        for item in value:
            _hash_value(hasher, item)
        hasher.update(b"]")
    elif isinstance(value, (set, frozenset)):
        hasher.update(b"<")
        for item in sorted(value, key=repr):
            _hash_value(hasher, item)
        hasher.update(b">")
    else:
        hasher.update(type(value).__name__.encode("utf-8", "replace"))
        hasher.update(b":")
        hasher.update(repr(value).encode("utf-8", "replace"))
        hasher.update(b";")


def report_revision_token(animal_data: Mapping[str, Any]) -> bytes:
    """Return a compact token covering every field used in daily Reports output."""
    relevant_values = tuple(animal_data.get(field) for field in _REVISION_FIELDS)
    try:
        # The C pickler walks large measurement histories much faster than a
        # Python callback per scalar.  The bytes are only an in-process change
        # detector; they are never persisted or deserialized.
        payload = pickle.dumps(relevant_values, protocol=pickle.HIGHEST_PROTOCOL)
        return hashlib.blake2b(payload, digest_size=16).digest()
    except (pickle.PickleError, TypeError, AttributeError):
        # Keep custom Mapping implementations usable in case a plugin places a
        # non-picklable value in a report-relevant field.
        pass

    hasher = hashlib.blake2b(digest_size=16)
    for field in _REVISION_FIELDS:
        _hash_value(hasher, field)
        _hash_value(hasher, animal_data.get(field))
    return hasher.digest()


def _parse_iso_date(value: Any) -> Optional[date]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return None


class AnimalReportIndex:
    """Date-indexed view of one animal record.

    Input order is retained within a day so rendered output remains identical
    to the historic list-scanning implementation.
    """

    def __init__(
        self,
        animal_data: Mapping[str, Any],
        normalize_event_type: Callable[[str], str],
        *,
        date_format: str,
    ) -> None:
        self.animal_data = animal_data
        self.normalize_event_type = normalize_event_type
        self.records_by_kind: Dict[str, Dict[date, List[Mapping[str, Any]]]] = {}
        self.years = set()
        progesterone_dates: List[date] = []
        weights: List[Tuple[datetime, Mapping[str, Any]]] = []

        for kind in ("daten", "pdg", "gewicht", "sperm"):
            by_date: Dict[date, List[Mapping[str, Any]]] = defaultdict(list)
            for record in animal_data.get(kind, []) or []:
                if not isinstance(record, Mapping):
                    continue
                timestamp = record.get("datum")
                if not isinstance(timestamp, datetime):
                    continue
                day = timestamp.date()
                by_date[day].append(record)
                self.years.add(timestamp.year)
                if kind == "daten":
                    progesterone_dates.append(day)
                elif kind == "gewicht":
                    weights.append((timestamp, record))
            self.records_by_kind[kind] = dict(by_date)

        progesterone_dates.sort()
        self.progesterone_dates = progesterone_dates
        weights.sort(key=lambda item: item[0])
        self.previous_weight_by_date: Dict[date, Mapping[str, Any]] = {}
        previous_record: Optional[Mapping[str, Any]] = None
        offset = 0
        while offset < len(weights):
            current_day = weights[offset][0].date()
            if previous_record is not None:
                self.previous_weight_by_date[current_day] = previous_record
            end = offset
            while end < len(weights) and weights[end][0].date() == current_day:
                end += 1
            previous_record = weights[end - 1][1]
            offset = end

        sick_start_raw = animal_data.get("sick_start_date")
        sick_end_raw = animal_data.get("sick_end_date")
        self.sick_start = _parse_iso_date(sick_start_raw)
        self.sick_end = _parse_iso_date(sick_end_raw)
        self.sick_period_valid = bool(sick_start_raw) and self.sick_start is not None
        if sick_end_raw and self.sick_end is None:
            self.sick_period_valid = False
        self.sick_days = set()
        for value in animal_data.get("sick_times", []) or []:
            if isinstance(value, datetime):
                self.sick_days.add(value.date())
            elif isinstance(value, str):
                parsed = _parse_iso_date(value)
                if parsed is not None:
                    self.sick_days.add(parsed)
        abnormal_start_raw = animal_data.get("abnormal_start_date")
        abnormal_end_raw = animal_data.get("abnormal_end_date")
        self.abnormal_start = _parse_iso_date(abnormal_start_raw)
        self.abnormal_end = _parse_iso_date(abnormal_end_raw)
        self.abnormal_period_valid = (
            bool(abnormal_start_raw) and self.abnormal_start is not None
        )
        if abnormal_end_raw and self.abnormal_end is None:
            self.abnormal_period_valid = False

        death_text = str(animal_data.get("death_date", "") or "").strip()
        self.death_date: Optional[date] = None
        if death_text:
            try:
                self.death_date = datetime.strptime(death_text, date_format).date()
            except (TypeError, ValueError):
                self.death_date = None

        unified_dates_by_type: Dict[str, set] = defaultdict(set)
        event_dates_by_type: Dict[str, List[date]] = defaultdict(list)
        exact_event_datetimes: Dict[str, List[datetime]] = defaultdict(list)
        ordered_occurrences: Dict[date, List[str]] = defaultdict(list)
        occurrence_notes: Dict[date, Dict[str, str]] = defaultdict(dict)

        for event in animal_data.get("events", []) or []:
            if not isinstance(event, Mapping):
                continue
            timestamp = event.get("datum")
            if not isinstance(timestamp, datetime):
                continue
            self.years.add(timestamp.year)
            raw_type = str(event.get("typ", "") or "")
            normalized_type = normalize_event_type(raw_type)
            day = timestamp.date()
            if normalized_type:
                unified_dates_by_type[normalized_type].add(day)
                event_dates_by_type[normalized_type].append(day)
            exact_event_datetimes[raw_type].append(timestamp)
            note = str(event.get("notiz", "") or "").strip()
            if normalized_type not in occurrence_notes[day]:
                ordered_occurrences[day].append(normalized_type)
                occurrence_notes[day][normalized_type] = note
            elif not occurrence_notes[day][normalized_type] and note:
                occurrence_notes[day][normalized_type] = note

        for legacy_type in LEGACY_EVENT_KEYS:
            normalized_type = normalize_event_type(legacy_type)
            for timestamp in animal_data.get(legacy_type, []) or []:
                if not isinstance(timestamp, datetime):
                    continue
                day = timestamp.date()
                if day not in unified_dates_by_type.get(normalized_type, set()):
                    event_dates_by_type[normalized_type].append(day)
                if normalized_type not in occurrence_notes[day]:
                    ordered_occurrences[day].append(normalized_type)
                    occurrence_notes[day][normalized_type] = ""

        self.event_dates_by_type = {
            event_type: sorted(days)
            for event_type, days in event_dates_by_type.items()
        }
        self.exact_event_datetimes = {
            event_type: sorted(timestamps)
            for event_type, timestamps in exact_event_datetimes.items()
        }
        self.occurrences_by_date = {
            day: [
                (event_type, occurrence_notes[day][event_type])
                for event_type in event_types
            ]
            for day, event_types in ordered_occurrences.items()
        }

        donor_recovery = [
            timestamp.date()
            for timestamp in animal_data.get("op", []) or []
            if isinstance(timestamp, datetime)
        ]
        donor_recovery.extend(
            record["datum"].date()
            for record in animal_data.get("sperm", []) or []
            if isinstance(record, Mapping)
            and isinstance(record.get("datum"), datetime)
        )
        self.donor_recovery_dates = sorted(donor_recovery)
        self.embryo_transfer_dates = sorted(
            timestamp.date()
            for timestamp in self.exact_event_datetimes.get(
                "embryo_transfer", []
            )
        )

    def records_on(self, kind: str, day: date) -> List[Mapping[str, Any]]:
        return self.records_by_kind.get(kind, {}).get(day, [])

    def progesterone_count_through(self, day: date) -> int:
        return bisect_right(self.progesterone_dates, day)

    def previous_weight_before(self, day: date) -> Optional[Mapping[str, Any]]:
        return self.previous_weight_by_date.get(day)

    def is_sick_on(self, day: date) -> bool:
        in_period = False
        if self.sick_period_valid and day >= self.sick_start:
            in_period = self.sick_end is None or day <= self.sick_end
        return in_period or day in self.sick_days

    def is_abnormal_on(self, day: date) -> bool:
        if not self.abnormal_period_valid or day < self.abnormal_start:
            return False
        return self.abnormal_end is None or day <= self.abnormal_end

    @staticmethod
    def _inside_recovery(
        sorted_dates: List[date], day: date, recovery_days: Any
    ) -> bool:
        index = bisect_right(sorted_dates, day) - 1
        if index < 0:
            return False
        event_day = sorted_dates[index]
        return event_day <= day <= event_day + timedelta(days=recovery_days)

    def is_in_recovery(self, role: str, day: date, recovery_days: Any) -> bool:
        if role in ("egg_cell_donor", "sperm_donor"):
            return self._inside_recovery(
                self.donor_recovery_dates, day, recovery_days
            )
        if role == "surrogate":
            return self._inside_recovery(
                self.embryo_transfer_dates, day, recovery_days
            )
        return False

    def event_counts_through(self, day: date) -> Dict[str, Tuple[int, int]]:
        return {
            event_type: (bisect_right(days, day), len(days))
            for event_type, days in self.event_dates_by_type.items()
        }

    def occurrences_on(self, day: date) -> List[Tuple[str, str]]:
        return self.occurrences_by_date.get(day, [])

    def latest_exact_event_before(
        self, event_type: str, check_datetime: datetime
    ) -> Optional[datetime]:
        values = self.exact_event_datetimes.get(event_type, [])
        index = bisect_right(values, check_datetime) - 1
        return values[index] if index >= 0 else None


class ReportIndexRepository:
    """Keep one index per animal revision and expose build counts for diagnostics."""

    def __init__(self) -> None:
        self._entries: Dict[str, Tuple[bytes, AnimalReportIndex]] = {}
        self.build_count = 0

    def get(
        self,
        animal_name: str,
        animal_data: Mapping[str, Any],
        normalize_event_type: Callable[[str], str],
        *,
        date_format: str,
    ) -> Tuple[AnimalReportIndex, bytes]:
        revision = report_revision_token(animal_data)
        cached = self._entries.get(str(animal_name))
        if cached is not None and cached[0] == revision:
            return cached[1], revision
        index = AnimalReportIndex(
            animal_data,
            normalize_event_type,
            date_format=date_format,
        )
        self._entries[str(animal_name)] = (revision, index)
        self.build_count += 1
        return index, revision

    def invalidate(self, animal_name: Optional[str] = None) -> None:
        if animal_name is None:
            self._entries.clear()
        else:
            self._entries.pop(str(animal_name), None)


class ReportJsonMTimeCache:
    """Read report edit JSON once until its mtime or size changes."""

    def __init__(self) -> None:
        self._path: Optional[Path] = None
        self._signature: Optional[Tuple[int, int]] = None
        self._data: Dict[str, Any] = {}
        self.read_count = 0

    @staticmethod
    def _file_signature(path: Path) -> Optional[Tuple[int, int]]:
        try:
            stat = path.stat()
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_size

    def load(self, path_like: Any) -> Dict[str, Any]:
        path = Path(path_like).resolve()
        signature = self._file_signature(path)
        if self._path == path and self._signature == signature:
            return self._data

        data: Dict[str, Any] = {}
        if signature is not None:
            try:
                content = path.read_text(encoding="utf-8").strip()
                parsed = json.loads(content) if content else {}
                if isinstance(parsed, dict):
                    data = parsed
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                logger.warning(
                    "Could not parse report data file %s: %s. Using empty data.",
                    path,
                    exc,
                )
        self._path = path
        self._signature = signature
        self._data = data
        self.read_count += 1
        return self._data

    def update_after_write(self, path_like: Any, data: Dict[str, Any]) -> None:
        path = Path(path_like).resolve()
        self._path = path
        self._signature = self._file_signature(path)
        self._data = data

    def invalidate(self) -> None:
        self._path = None
        self._signature = None
        self._data = {}
