from __future__ import annotations

import csv
import json
from datetime import datetime
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    from .deployment_governance_log_search import (
        GovernanceLogSearchService,
    )

_CSV_FIELDNAMES = (
    "timestamp",
    "level",
    "component",
    "event",
    "fields_json",
)


class GovernanceLogExportService:
    """
    Exports governance log history for offline analysis and
    incident reports.

    Reads through a GovernanceLogSearchService rather than holding
    its own state: this service only formats and writes, it never
    records or persists anything. Every export method streams
    entries to the given stream one at a time (via
    GovernanceLogSearchService.iter_search) rather than
    materializing the full export in memory first, so exporting a
    large history does not require it to fit in memory all at once.
    Entries are written in the order iter_search yields them (newest
    first) and are never reordered.
    """

    def __init__(
        self,
        search_service: "GovernanceLogSearchService",
    ) -> None:
        self._search_service = search_service

    def export_json(
        self,
        stream: TextIO,
        *,
        level: str | None = None,
        component: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> int:
        """
        Write matching entries to stream as a single JSON array, one
        entry object per line, and return the number written.

        Each entry's fields are written in GovernanceLogEntry.to_dict()'s
        fixed order (timestamp, level, component, event, fields)
        rather than sorted, and timestamps are UTC ISO-8601 strings.
        """

        stream.write("[\n")

        count = 0

        for entry in self._search_service.iter_search(
            level=level,
            component=component,
            since=since,
            until=until,
        ):
            if count:
                stream.write(",\n")

            stream.write(json.dumps(entry.to_dict(), ensure_ascii=False))

            count += 1

        stream.write("\n]\n" if count else "]\n")

        return count

    def export_ndjson(
        self,
        stream: TextIO,
        *,
        level: str | None = None,
        component: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> int:
        """
        Write matching entries to stream as newline-delimited JSON
        (one compact JSON object per line, no enclosing array), and
        return the number written. Suited to streaming consumers
        that process one line at a time.
        """

        count = 0

        for entry in self._search_service.iter_search(
            level=level,
            component=component,
            since=since,
            until=until,
        ):
            stream.write(
                json.dumps(entry.to_dict(), ensure_ascii=False)
            )

            stream.write("\n")

            count += 1

        return count

    def export_csv(
        self,
        stream: TextIO,
        *,
        level: str | None = None,
        component: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> int:
        """
        Write matching entries to stream as CSV, with a fixed
        deterministic column order
        (timestamp, level, component, event, fields_json), and
        return the number written. The structured fields mapping is
        JSON-encoded into the single fields_json column, since CSV
        has no native representation for nested data.
        """

        writer = csv.DictWriter(stream, fieldnames=_CSV_FIELDNAMES)

        writer.writeheader()

        count = 0

        for entry in self._search_service.iter_search(
            level=level,
            component=component,
            since=since,
            until=until,
        ):
            writer.writerow(
                {
                    "timestamp": entry.timestamp.isoformat(),
                    "level": entry.level,
                    "component": entry.component,
                    "event": entry.event,
                    "fields_json": json.dumps(
                        dict(entry.fields), ensure_ascii=False
                    ),
                }
            )

            count += 1

        return count
