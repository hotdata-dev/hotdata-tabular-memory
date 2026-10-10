"""Memory as one append-only table, in the database the project already queries.

A record is never edited. A correction appends a new record naming the one it
replaces, so the current set is every record nothing supersedes, and the history
stays readable — which is how the benchmark's worst agent was diagnosed at all.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import hotdata

TABLE = "agent_memory"
KINDS = ("fact", "profile", "procedure")

# A record carrying several results and no query to reproduce them is a cached
# answer, not a memory: it was true of one query on one day.
FIGURE = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w])")
FIGURE_LIMIT = 3


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize(text: str) -> str:
    return " ".join(text.lower().split())


def figures(content: str) -> list[str]:
    """Return the numbers in `content` that read as results rather than labels."""
    found = []
    for match in FIGURE.finditer(content):
        text = match.group(0)
        if len(text.replace(",", "").replace(".", "")) >= 3 or "." in text:
            found.append(text)
    return found


def reads_as_a_conclusion(content: str) -> bool:
    return len(figures(content)) >= FIGURE_LIMIT


@dataclass(frozen=True)
class Record:
    namespace: str
    subject: str
    kind: str
    content: str
    query: str = ""
    tags: str = ""
    sources: str = ""
    actor: str = ""
    supersedes: str = ""
    created_at: str = field(default_factory=now)
    id: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        if not normalize(self.content):
            raise ValueError("content must not be empty")
        if not self.id:
            digest = hashlib.sha256(
                f"{self.namespace}\x00{self.subject}\x00{normalize(self.content)}"
                .encode()).hexdigest()[:16]
            object.__setattr__(self, "id", f"{self.subject or 'note'}-{digest}")

    def line(self) -> str:
        """Return the one line this record contributes to a memory block."""
        parts = [self.content.replace("\n", " ").strip()]
        if self.query:
            parts.append(f"[reproduce: {self.query}]")
        if self.sources:
            parts.append(f"[sources: {self.sources}]")
        return "- " + " ".join(parts)


class Store:
    """Records in a Hotdata table, read and written through the CLI."""

    def __init__(self, database: str, catalog: str) -> None:
        self.database = database
        self.catalog = catalog
        self._ready = False

    @property
    def qualified(self) -> str:
        return f"{self.catalog}.public.{TABLE}"

    def _ensure(self) -> bool:
        if not self._ready:
            self._ready = hotdata.table_exists(self.catalog, TABLE, self.database)
        return self._ready

    def current(self, namespace: str, limit: int = 200) -> list[Record]:
        """Return every record under `namespace` that nothing supersedes."""
        if not self._ensure():
            return []
        where = f"namespace = {hotdata.quote(namespace)}"
        sql = (
            f"SELECT namespace, subject, kind, content, query, tags, sources, actor, "
            f"supersedes, created_at, id FROM {self.qualified} m WHERE {where} "
            f"AND id NOT IN (SELECT supersedes FROM {self.qualified} "
            f"WHERE {where} AND supersedes <> '') "
            f"ORDER BY created_at DESC LIMIT {int(limit)}"
        )
        return [Record(**row) for row in hotdata.query(sql, self.database)]

    def by_subject(self, namespace: str, subject: str) -> list[Record]:
        return [r for r in self.current(namespace) if r.subject == subject]

    def search(self, namespace: str, text: str, limit: int = 10) -> list[Record]:
        """Return records whose content matches every word of `text`.

        Plain matching, deliberately: the store is small, and a BM25 index on a
        table this size buys nothing a LIKE does not.
        """
        words = [w for w in re.findall(r"[A-Za-z0-9]+", text.lower()) if len(w) > 2]
        found = self.current(namespace)
        if not words:
            return found[:limit]
        scored = []
        for record in found:
            body = normalize(record.content + " " + record.subject)
            hits = sum(1 for w in words if w in body)
            if hits:
                scored.append((hits, record))
        scored.sort(key=lambda pair: -pair[0])
        return [record for _, record in scored[:limit]]

    def write(self, records: Sequence[Record]) -> list[str]:
        """Append `records`, skipping any whose id is already present."""
        if not records:
            return []
        known = {r.id for r in self.current(records[0].namespace, limit=500)}
        fresh = [r for r in records if r.id not in known]
        if fresh:
            hotdata.append([asdict(r) for r in fresh], TABLE, self.database)
            self._ready = True
        return [r.id for r in records]

    def forget(self, namespace: str, ids: Sequence[str]) -> list[str]:
        """Delete the named records outright. History goes with them."""
        if not ids or not self._ensure():
            return []
        rows = [{"id": i, "namespace": namespace} for i in ids]
        hotdata.append(rows, TABLE, self.database, mode="delete", key="id")
        return [i for i in ids if i not in {r.id for r in self.current(namespace, 500)}]


class FileStore(Store):
    """The same operations over a JSON file, for a project with no database."""

    def __init__(self, path: Path) -> None:  # noqa: D107
        self.path = path
        self._ready = True

    def _load(self) -> list[Record]:
        if not self.path.exists():
            return []
        return [Record(**row) for row in json.loads(self.path.read_text("utf-8"))]

    def _save(self, records: Sequence[Record]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps([asdict(r) for r in records], indent=1), "utf-8")
        temporary.replace(self.path)

    def current(self, namespace: str, limit: int = 200) -> list[Record]:
        records = [r for r in self._load() if r.namespace == namespace]
        replaced = {r.supersedes for r in records if r.supersedes}
        live = [r for r in records if r.id not in replaced]
        live.sort(key=lambda r: r.created_at, reverse=True)
        return live[:limit]

    def write(self, records: Sequence[Record]) -> list[str]:
        held = self._load()
        known = {r.id for r in held}
        self._save(held + [r for r in records if r.id not in known])
        return [r.id for r in records]

    def forget(self, namespace: str, ids: Sequence[str]) -> list[str]:
        held = self._load()
        gone = {i for i in ids}
        self._save([r for r in held if not (r.namespace == namespace and r.id in gone)])
        return list(ids)
