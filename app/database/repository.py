import sqlite3
import threading
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from app.errors import NotFoundError, UserInputError
from app.models import Capsule, Delivery, Settings, Stats, Tag
from app.timeutils import dump_dt, parse_dt

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

RESPONSE_VALUES = {"useful", "completed", "outdated", "still_relevant"}


class Repository:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def migrate(self) -> None:
        script = SCHEMA_PATH.read_text(encoding="utf-8")
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(script)
            finally:
                conn.close()

    def ensure_settings(self, user_id: int, now: datetime) -> Settings:
        with self._transaction() as conn:
            self._ensure_settings(conn, user_id, now)
            return self._settings(conn, user_id)

    def get_settings(self, user_id: int) -> Settings:
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM settings WHERE user_id = ?", (user_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError("Set up your archive with /start first.")
            return self._settings_from_row(row)

    def update_settings(self, user_id: int, **fields) -> Settings:
        allowed = {"timezone", "daily_limit", "discovery_enabled", "delivery_time"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Unknown settings: {', '.join(sorted(unknown))}")
        with self._transaction() as conn:
            current = conn.execute(
                "SELECT user_id FROM settings WHERE user_id = ?", (user_id,)
            ).fetchone()
            if current is None:
                raise NotFoundError("Set up your archive with /start first.")
            assignments = []
            values: list[object] = []
            for key, value in fields.items():
                if key == "discovery_enabled":
                    value = 1 if value else 0
                assignments.append(f"{key} = ?")
                values.append(value)
            values.append(user_id)
            conn.execute(
                f"UPDATE settings SET {', '.join(assignments)} WHERE user_id = ?",
                values,
            )
            return self._settings(conn, user_id)

    def all_settings(self) -> list[Settings]:
        with self._transaction() as conn:
            rows = conn.execute("SELECT * FROM settings ORDER BY user_id").fetchall()
            return [self._settings_from_row(row) for row in rows]

    def set_last_discovery_date(self, user_id: int, day: str) -> None:
        with self._transaction() as conn:
            conn.execute(
                "UPDATE settings SET last_discovery_date = ? WHERE user_id = ?",
                (day, user_id),
            )

    def insert_capsule(
        self,
        *,
        user_id: int,
        kind: str,
        text_content: str | None,
        url: str | None,
        created_at: datetime,
        tags: list[str],
        file_path: str | None = None,
        file_name: str | None = None,
        source_chat_id: int | None = None,
        source_message_id: int | None = None,
        now: datetime,
    ) -> Capsule:
        if file_path is not None:
            _require_relative(file_path)
        with self._transaction() as conn:
            self._ensure_settings(conn, user_id, now)
            if source_chat_id is not None and source_message_id is not None:
                existing = conn.execute(
                    """
                    SELECT id FROM capsules
                    WHERE user_id = ? AND source_chat_id = ? AND source_message_id = ?
                    """,
                    (user_id, source_chat_id, source_message_id),
                ).fetchone()
                if existing is not None:
                    raise UserInputError("That message is already in the vault.")
            public_id = self._allocate_public_id(conn, user_id)
            cursor = conn.execute(
                """
                INSERT INTO capsules (
                    public_id, user_id, kind, text_content, url, file_path, file_name,
                    created_at, unlock_at, release_mode, status, source_chat_id, source_message_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 'open', 'active', ?, ?)
                """,
                (
                    public_id,
                    user_id,
                    kind,
                    text_content,
                    url,
                    file_path,
                    file_name,
                    dump_dt(created_at),
                    source_chat_id,
                    source_message_id,
                ),
            )
            capsule_id = int(cursor.lastrowid)
            for name in tags:
                self._link_tag(conn, user_id, capsule_id, name)
            return self._capsule(conn, user_id, capsule_id)

    def get(self, user_id: int, capsule_id: int) -> Capsule | None:
        with self._transaction() as conn:
            return self._capsule_or_none(conn, user_id, capsule_id)

    def get_by_public_id(self, user_id: int, public_id: str) -> Capsule | None:
        with self._transaction() as conn:
            row = conn.execute(
                """
                SELECT * FROM capsules
                WHERE user_id = ? AND public_id = ? AND status = 'active'
                """,
                (user_id, public_id),
            ).fetchone()
            if row is None:
                return None
            return self._with_tags(conn, [self._capsule_from_row(row)])[0]

    def list_capsules(self, user_id: int, *, limit: int, offset: int) -> list[Capsule]:
        with self._transaction() as conn:
            rows = conn.execute(
                """
                SELECT * FROM capsules
                WHERE user_id = ? AND status = 'active'
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (user_id, limit, offset),
            ).fetchall()
            return self._with_tags(conn, [self._capsule_from_row(row) for row in rows])

    def count_capsules(self, user_id: int) -> int:
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM capsules WHERE user_id = ? AND status = 'active'",
                (user_id,),
            ).fetchone()
            return int(row["n"])

    def count_sealed(self, user_id: int, now: datetime) -> int:
        with self._transaction() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n FROM capsules
                WHERE user_id = ? AND status = 'active' AND release_mode = 'sealed'
                  AND unlock_at IS NOT NULL AND unlock_at > ?
                """,
                (user_id, dump_dt(now)),
            ).fetchone()
            return int(row["n"])

    def update_release(
        self,
        user_id: int,
        capsule_id: int,
        release_mode: str,
        unlock_at: datetime | None,
    ) -> Capsule:
        with self._transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE capsules
                SET release_mode = ?, unlock_at = ?
                WHERE id = ? AND user_id = ? AND status = 'active'
                """,
                (
                    release_mode,
                    dump_dt(unlock_at) if unlock_at else None,
                    capsule_id,
                    user_id,
                ),
            )
            if cursor.rowcount != 1:
                raise NotFoundError("No capsule with that id in your vault.")
            return self._capsule(conn, user_id, capsule_id)

    def delete_capsule(self, user_id: int, capsule_id: int) -> Capsule:
        with self._transaction() as conn:
            capsule = self._capsule_or_none(conn, user_id, capsule_id)
            if capsule is None:
                raise NotFoundError("No capsule with that id in your vault.")
            conn.execute(
                "DELETE FROM capsules WHERE id = ? AND user_id = ?",
                (capsule_id, user_id),
            )
            return capsule

    def add_tag(self, user_id: int, capsule_id: int, name: str) -> Capsule:
        with self._transaction() as conn:
            if self._capsule_or_none(conn, user_id, capsule_id) is None:
                raise NotFoundError("No capsule with that id in your vault.")
            self._link_tag(conn, user_id, capsule_id, name)
            return self._capsule(conn, user_id, capsule_id)

    def list_tags(self, user_id: int) -> list[Tag]:
        with self._transaction() as conn:
            rows = conn.execute(
                """
                SELECT t.id, t.user_id, t.name, COUNT(ct.capsule_id) AS n
                FROM tags t
                LEFT JOIN capsule_tags ct ON ct.tag_id = t.id
                WHERE t.user_id = ?
                GROUP BY t.id
                ORDER BY n DESC, t.name
                """,
                (user_id,),
            ).fetchall()
            return [
                Tag(id=row["id"], user_id=row["user_id"], name=row["name"], count=row["n"])
                for row in rows
            ]

    def get_tag(self, user_id: int, tag_id: int) -> Tag | None:
        with self._transaction() as conn:
            row = conn.execute(
                """
                SELECT t.id, t.user_id, t.name, COUNT(ct.capsule_id) AS n
                FROM tags t
                LEFT JOIN capsule_tags ct ON ct.tag_id = t.id
                WHERE t.user_id = ? AND t.id = ?
                GROUP BY t.id
                """,
                (user_id, tag_id),
            ).fetchone()
            if row is None:
                return None
            return Tag(id=row["id"], user_id=row["user_id"], name=row["name"], count=row["n"])

    def delete_tag(self, user_id: int, tag_id: int) -> str:
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT name FROM tags WHERE id = ? AND user_id = ?",
                (tag_id, user_id),
            ).fetchone()
            if row is None:
                raise NotFoundError("That tag is not in your archive.")
            conn.execute("DELETE FROM tags WHERE id = ? AND user_id = ?", (tag_id, user_id))
            return str(row["name"])

    def capsules_by_tag(self, user_id: int, tag_name: str, *, limit: int = 10) -> list[Capsule]:
        with self._transaction() as conn:
            rows = conn.execute(
                """
                SELECT c.* FROM capsules c
                JOIN capsule_tags ct ON ct.capsule_id = c.id
                JOIN tags t ON t.id = ct.tag_id
                WHERE c.user_id = ? AND t.user_id = ? AND t.name = ? AND c.status = 'active'
                ORDER BY c.created_at DESC
                LIMIT ?
                """,
                (user_id, user_id, tag_name, limit),
            ).fetchall()
            return self._with_tags(conn, [self._capsule_from_row(row) for row in rows])

    def search(self, user_id: int, query: str, *, limit: int = 10) -> list[Capsule]:
        with self._transaction() as conn:
            found: list[Capsule] = []
            seen: set[int] = set()
            public = _public_id_or_none(query)
            if public:
                row = conn.execute(
                    """
                    SELECT * FROM capsules
                    WHERE user_id = ? AND public_id = ? AND status = 'active'
                    """,
                    (user_id, public),
                ).fetchone()
                if row is not None:
                    capsule = self._capsule_from_row(row)
                    found.append(capsule)
                    seen.add(capsule.id)
            try:
                conn.execute("SAVEPOINT fts")
                rows = conn.execute(
                    """
                    SELECT c.* FROM capsules_fts f
                    JOIN capsules c ON c.id = f.rowid
                    WHERE capsules_fts MATCH ? AND c.user_id = ? AND c.status = 'active'
                    ORDER BY bm25(capsules_fts), c.created_at DESC
                    LIMIT ?
                    """,
                    (_fts_query(query), user_id, limit),
                ).fetchall()
                conn.execute("RELEASE fts")
            except sqlite3.OperationalError:
                conn.execute("ROLLBACK TO fts")
                conn.execute("RELEASE fts")
                like = _like_query(query)
                rows = conn.execute(
                    """
                    SELECT * FROM capsules
                    WHERE user_id = ? AND status = 'active'
                      AND (
                        text_content LIKE ? ESCAPE '\\'
                        OR url LIKE ? ESCAPE '\\'
                        OR public_id LIKE ? ESCAPE '\\'
                      )
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (user_id, like, like, like, limit),
                ).fetchall()
            for row in rows:
                capsule = self._capsule_from_row(row)
                if capsule.id in seen:
                    continue
                found.append(capsule)
                seen.add(capsule.id)
                if len(found) >= limit:
                    break
            return self._with_tags(conn, found[:limit])

    def due_scheduled(self, now: datetime, user_id: int | None = None) -> list[Capsule]:
        with self._transaction() as conn:
            sql = """
                SELECT c.* FROM capsules c
                WHERE c.status = 'active'
                  AND c.release_mode = 'sealed'
                  AND c.unlock_at IS NOT NULL
                  AND c.unlock_at <= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM deliveries d
                      WHERE d.capsule_id = c.id
                        AND d.kind = 'scheduled'
                        AND d.state IN ('sent', 'pending')
                  )
            """
            params: list[object] = [dump_dt(now)]
            if user_id is not None:
                sql += " AND c.user_id = ?"
                params.append(user_id)
            sql += " ORDER BY c.unlock_at, c.id"
            rows = conn.execute(sql, params).fetchall()
            return self._with_tags(conn, [self._capsule_from_row(row) for row in rows])

    def eligible_items(self, user_id: int, now: datetime, min_age_days: int) -> list[Capsule]:
        created_before = dump_dt(now - timedelta(days=min_age_days))
        with self._transaction() as conn:
            rows = conn.execute(
                """
                SELECT * FROM capsules
                WHERE user_id = ?
                  AND status = 'active'
                  AND created_at <= ?
                  AND (unlock_at IS NULL OR unlock_at <= ?)
                  AND NOT (
                      release_mode = 'sealed'
                      AND NOT EXISTS (
                          SELECT 1 FROM deliveries d
                          WHERE d.capsule_id = capsules.id
                            AND d.kind = 'scheduled'
                            AND d.state = 'sent'
                      )
                  )
                ORDER BY created_at, id
                """,
                (user_id, created_before, dump_dt(now)),
            ).fetchall()
            return self._with_tags(conn, [self._capsule_from_row(row) for row in rows])

    def recent_delivery_ids(self, user_id: int, now: datetime, cooldown_days: int) -> set[int]:
        since = dump_dt(now - timedelta(days=cooldown_days))
        with self._transaction() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT capsule_id FROM deliveries
                WHERE user_id = ? AND state = 'sent' AND delivered_at >= ?
                """,
                (user_id, since),
            ).fetchall()
            return {int(row["capsule_id"]) for row in rows}

    def count_rediscovery_between(self, user_id: int, start: datetime, end: datetime) -> int:
        with self._transaction() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n FROM deliveries
                WHERE user_id = ? AND kind = 'rediscovery' AND state IN ('sent', 'pending')
                  AND delivered_at >= ? AND delivered_at < ?
                """,
                (user_id, dump_dt(start), dump_dt(end)),
            ).fetchone()
            return int(row["n"])

    def fail_stale_pending(self, now: datetime, *, older_than_seconds: int = 120) -> int:
        cutoff = dump_dt(now - timedelta(seconds=older_than_seconds))
        with self._transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE deliveries SET state = 'failed'
                WHERE state = 'pending' AND delivered_at < ?
                """,
                (cutoff,),
            )
            return int(cursor.rowcount)

    def claim_delivery(self, user_id: int, capsule_id: int, kind: str, now: datetime) -> int | None:
        with self._transaction() as conn:
            owned = conn.execute(
                """
                SELECT id FROM capsules
                WHERE id = ? AND user_id = ? AND status = 'active'
                """,
                (capsule_id, user_id),
            ).fetchone()
            if owned is None:
                return None
            if kind == "scheduled":
                existing = conn.execute(
                    """
                    SELECT id FROM deliveries
                    WHERE capsule_id = ? AND kind = 'scheduled' AND state IN ('sent', 'pending')
                    """,
                    (capsule_id,),
                ).fetchone()
                if existing is not None:
                    return None
            cursor = conn.execute(
                """
                INSERT INTO deliveries (
                    capsule_id, user_id, delivered_at, kind, response, state
                ) VALUES (?, ?, ?, ?, NULL, 'pending')
                """,
                (capsule_id, user_id, dump_dt(now), kind),
            )
            return int(cursor.lastrowid)

    def mark_delivery(
        self, delivery_id: int, state: str, telegram_message_id: int | None = None
    ) -> None:
        with self._transaction() as conn:
            conn.execute(
                """
                UPDATE deliveries
                SET state = ?, telegram_message_id = COALESCE(?, telegram_message_id)
                WHERE id = ?
                """,
                (state, telegram_message_id, delivery_id),
            )

    def set_response(self, user_id: int, delivery_id: int, response: str, now: datetime) -> bool:
        if response not in RESPONSE_VALUES:
            raise UserInputError("Unknown response.")
        with self._transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE deliveries
                SET response = ?, responded_at = ?
                WHERE id = ? AND user_id = ? AND state = 'sent'
                """,
                (response, dump_dt(now), delivery_id, user_id),
            )
            return cursor.rowcount == 1

    def deliveries_between(self, user_id: int, start: datetime, end: datetime) -> list[Delivery]:
        with self._transaction() as conn:
            rows = conn.execute(
                """
                SELECT d.*, c.public_id FROM deliveries d
                JOIN capsules c ON c.id = d.capsule_id
                WHERE d.user_id = ? AND d.state = 'sent'
                  AND d.delivered_at >= ? AND d.delivered_at < ?
                ORDER BY d.delivered_at, d.id
                """,
                (user_id, dump_dt(start), dump_dt(end)),
            ).fetchall()
            return [self._delivery_from_row(row) for row in rows]

    def stats(self, user_id: int) -> Stats:
        with self._transaction() as conn:
            counts = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN release_mode = 'sealed' AND unlock_at IS NOT NULL THEN 1 ELSE 0 END) AS sealed,
                    MIN(created_at) AS oldest
                FROM capsules
                WHERE user_id = ? AND status = 'active'
                """,
                (user_id,),
            ).fetchone()
            tags = conn.execute(
                "SELECT COUNT(*) AS n FROM tags WHERE user_id = ?", (user_id,)
            ).fetchone()
            deliveries = conn.execute(
                """
                SELECT kind, COUNT(*) AS n FROM deliveries
                WHERE user_id = ? AND state = 'sent'
                GROUP BY kind
                """,
                (user_id,),
            ).fetchall()
            responses = conn.execute(
                """
                SELECT response, COUNT(*) AS n FROM deliveries
                WHERE user_id = ? AND state = 'sent' AND response IS NOT NULL
                GROUP BY response
                """,
                (user_id,),
            ).fetchall()
            by_kind = {row["kind"]: int(row["n"]) for row in deliveries}
            total = int(counts["total"] or 0)
            sealed = int(counts["sealed"] or 0)
            return Stats(
                capsule_count=total,
                open_count=total - sealed,
                sealed_count=sealed,
                tag_count=int(tags["n"]),
                rediscovery_count=by_kind.get("rediscovery", 0),
                scheduled_count=by_kind.get("scheduled", 0),
                oldest_at=parse_dt(counts["oldest"]) if counts["oldest"] else None,
                responses={row["response"]: int(row["n"]) for row in responses},
            )

    def export_user(self, user_id: int) -> dict:
        with self._transaction() as conn:
            settings = conn.execute(
                "SELECT * FROM settings WHERE user_id = ?", (user_id,)
            ).fetchone()
            capsules = conn.execute(
                "SELECT * FROM capsules WHERE user_id = ? ORDER BY id", (user_id,)
            ).fetchall()
            tags = conn.execute(
                "SELECT * FROM tags WHERE user_id = ? ORDER BY id", (user_id,)
            ).fetchall()
            links = conn.execute(
                """
                SELECT ct.capsule_id, ct.tag_id FROM capsule_tags ct
                JOIN capsules c ON c.id = ct.capsule_id
                WHERE c.user_id = ?
                """,
                (user_id,),
            ).fetchall()
            deliveries = conn.execute(
                "SELECT * FROM deliveries WHERE user_id = ? ORDER BY id", (user_id,)
            ).fetchall()
            return {
                "settings": dict(settings) if settings else None,
                "capsules": [dict(row) for row in capsules],
                "tags": [dict(row) for row in tags],
                "capsule_tags": [dict(row) for row in links],
                "deliveries": [dict(row) for row in deliveries],
            }

    def replace_user_archive(self, user_id: int, payload: dict, now: datetime) -> int:
        with self._transaction() as conn:
            self._ensure_settings(conn, user_id, now)
            conn.execute(
                """
                DELETE FROM deliveries WHERE capsule_id IN (
                    SELECT id FROM capsules WHERE user_id = ?
                ) OR user_id = ?
                """,
                (user_id, user_id),
            )
            conn.execute("DELETE FROM capsules WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM tags WHERE user_id = ?", (user_id,))
            settings = payload.get("settings") or {}
            conn.execute(
                """
                UPDATE settings
                SET timezone = ?, daily_limit = ?, discovery_enabled = ?, delivery_time = ?
                WHERE user_id = ?
                """,
                (
                    settings.get("timezone") or "UTC",
                    int(settings.get("daily_limit") or 1),
                    1 if settings.get("discovery_enabled", 1) else 0,
                    settings.get("delivery_time") or "09:00",
                    user_id,
                ),
            )
            id_map: dict[int, int] = {}
            for row in payload.get("capsules") or []:
                cursor = conn.execute(
                    """
                    INSERT INTO capsules (
                        public_id, user_id, kind, text_content, url, file_path, file_name,
                        created_at, unlock_at, release_mode, status, source_chat_id, source_message_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                    """,
                    (
                        row["public_id"],
                        user_id,
                        row["kind"],
                        row.get("text_content"),
                        row.get("url"),
                        row.get("file_path"),
                        row.get("file_name"),
                        row["created_at"],
                        row.get("unlock_at"),
                        row.get("release_mode") or "open",
                        row.get("source_chat_id"),
                        row.get("source_message_id"),
                    ),
                )
                id_map[int(row["id"])] = int(cursor.lastrowid)
            tag_map: dict[int, int] = {}
            for row in payload.get("tags") or []:
                cursor = conn.execute(
                    "INSERT INTO tags (user_id, name) VALUES (?, ?)",
                    (user_id, row["name"]),
                )
                tag_map[int(row["id"])] = int(cursor.lastrowid)
            for link in payload.get("capsule_tags") or []:
                capsule_id = id_map.get(int(link["capsule_id"]))
                tag_id = tag_map.get(int(link["tag_id"]))
                if capsule_id is None or tag_id is None:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO capsule_tags (capsule_id, tag_id) VALUES (?, ?)",
                    (capsule_id, tag_id),
                )
            for row in payload.get("deliveries") or []:
                capsule_id = id_map.get(int(row["capsule_id"]))
                if capsule_id is None:
                    continue
                conn.execute(
                    """
                    INSERT INTO deliveries (
                        capsule_id, user_id, delivered_at, kind, response, state,
                        telegram_message_id, responded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
                    """,
                    (
                        capsule_id,
                        user_id,
                        row["delivered_at"],
                        row["kind"],
                        row.get("response"),
                        row.get("state") or "sent",
                        row.get("responded_at"),
                    ),
                )
            self._sync_counter(conn, user_id)
            return len(id_map)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _transaction(self):
        return _Transaction(self)

    def _ensure_settings(self, conn: sqlite3.Connection, user_id: int, now: datetime) -> None:
        conn.execute(
            """
            INSERT OR IGNORE INTO settings (
                user_id, timezone, daily_limit, discovery_enabled, delivery_time, created_at
            ) VALUES (?, 'UTC', 1, 1, '09:00', ?)
            """,
            (user_id, dump_dt(now)),
        )

    def _settings(self, conn: sqlite3.Connection, user_id: int) -> Settings:
        row = conn.execute("SELECT * FROM settings WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            raise NotFoundError("Set up your archive with /start first.")
        return self._settings_from_row(row)

    def _settings_from_row(self, row: sqlite3.Row) -> Settings:
        return Settings(
            user_id=int(row["user_id"]),
            timezone=row["timezone"],
            daily_limit=int(row["daily_limit"]),
            discovery_enabled=bool(row["discovery_enabled"]),
            delivery_time=row["delivery_time"],
            last_discovery_date=row["last_discovery_date"],
            created_at=parse_dt(row["created_at"]),
        )

    def _allocate_public_id(self, conn: sqlite3.Connection, user_id: int) -> str:
        row = conn.execute(
            "SELECT next_number FROM user_counters WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            number = 1
            conn.execute(
                "INSERT INTO user_counters (user_id, next_number) VALUES (?, 2)",
                (user_id,),
            )
        else:
            number = int(row["next_number"])
            conn.execute(
                "UPDATE user_counters SET next_number = ? WHERE user_id = ?",
                (number + 1, user_id),
            )
        return format_public_id(number)

    def _sync_counter(self, conn: sqlite3.Connection, user_id: int) -> None:
        rows = conn.execute(
            "SELECT public_id FROM capsules WHERE user_id = ?", (user_id,)
        ).fetchall()
        highest = 0
        for row in rows:
            highest = max(highest, _public_number(row["public_id"]))
        conn.execute(
            """
            INSERT INTO user_counters (user_id, next_number) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET next_number = excluded.next_number
            """,
            (user_id, highest + 1),
        )

    def _link_tag(self, conn: sqlite3.Connection, user_id: int, capsule_id: int, name: str) -> None:
        conn.execute(
            "INSERT OR IGNORE INTO tags (user_id, name) VALUES (?, ?)",
            (user_id, name),
        )
        tag = conn.execute(
            "SELECT id FROM tags WHERE user_id = ? AND name = ?",
            (user_id, name),
        ).fetchone()
        conn.execute(
            "INSERT OR IGNORE INTO capsule_tags (capsule_id, tag_id) VALUES (?, ?)",
            (capsule_id, int(tag["id"])),
        )

    def _capsule(self, conn: sqlite3.Connection, user_id: int, capsule_id: int) -> Capsule:
        capsule = self._capsule_or_none(conn, user_id, capsule_id)
        if capsule is None:
            raise NotFoundError("No capsule with that id in your vault.")
        return capsule

    def _capsule_or_none(
        self, conn: sqlite3.Connection, user_id: int, capsule_id: int
    ) -> Capsule | None:
        row = conn.execute(
            """
            SELECT * FROM capsules
            WHERE id = ? AND user_id = ? AND status = 'active'
            """,
            (capsule_id, user_id),
        ).fetchone()
        if row is None:
            return None
        return self._with_tags(conn, [self._capsule_from_row(row)])[0]

    def _capsule_from_row(self, row: sqlite3.Row) -> Capsule:
        return Capsule(
            id=int(row["id"]),
            public_id=row["public_id"],
            user_id=int(row["user_id"]),
            kind=row["kind"],
            text_content=row["text_content"],
            url=row["url"],
            file_path=row["file_path"],
            file_name=row["file_name"],
            created_at=parse_dt(row["created_at"]),
            unlock_at=parse_dt(row["unlock_at"]) if row["unlock_at"] else None,
            release_mode=row["release_mode"],
            status=row["status"],
            source_chat_id=row["source_chat_id"],
            source_message_id=row["source_message_id"],
        )

    def _with_tags(self, conn: sqlite3.Connection, capsules: list[Capsule]) -> list[Capsule]:
        if not capsules:
            return []
        ids = [capsule.id for capsule in capsules]
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"""
            SELECT ct.capsule_id, t.name
            FROM capsule_tags ct
            JOIN tags t ON t.id = ct.tag_id
            WHERE ct.capsule_id IN ({placeholders})
            ORDER BY t.name
            """,
            ids,
        ).fetchall()
        names: dict[int, list[str]] = defaultdict(list)
        for row in rows:
            names[int(row["capsule_id"])].append(row["name"])
        return [replace(capsule, tags=tuple(names.get(capsule.id, ()))) for capsule in capsules]

    def _delivery_from_row(self, row: sqlite3.Row) -> Delivery:
        return Delivery(
            id=int(row["id"]),
            capsule_id=int(row["capsule_id"]),
            user_id=int(row["user_id"]),
            delivered_at=parse_dt(row["delivered_at"]),
            kind=row["kind"],
            response=row["response"],
            state=row["state"],
            public_id=row["public_id"] if "public_id" in row.keys() else None,
            responded_at=parse_dt(row["responded_at"]) if row["responded_at"] else None,
        )


class _Transaction:
    def __init__(self, repository: Repository):
        self._repository = repository
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> sqlite3.Connection:
        self._repository._lock.acquire()
        self._conn = self._repository._connect()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
        except Exception:
            self._conn.close()
            self._repository._lock.release()
            raise
        return self._conn

    def __exit__(self, exc_type, exc, tb) -> None:
        assert self._conn is not None
        try:
            if exc_type is None:
                self._conn.execute("COMMIT")
            else:
                self._conn.execute("ROLLBACK")
        finally:
            self._conn.close()
            self._repository._lock.release()


def format_public_id(number: int) -> str:
    if number < 10000:
        return f"TC-{number:04d}"
    return f"TC-{number}"


def normalize_public_id(raw: str) -> str:
    text = raw.strip().upper()
    if text.startswith("TC-"):
        text = text[3:]
    if not text.isdigit():
        raise UserInputError("Use an id like TC-0042.")
    number = int(text)
    if number < 1:
        raise UserInputError("Use an id like TC-0042.")
    return format_public_id(number)


def _public_number(public_id: str) -> int:
    try:
        return int(public_id.split("-", 1)[1])
    except (IndexError, ValueError):
        return 0


def _public_id_or_none(query: str) -> str | None:
    try:
        return normalize_public_id(query)
    except UserInputError:
        return None


def _fts_query(query: str) -> str:
    cleaned = " ".join(query.replace('"', " ").split())
    if not cleaned:
        raise UserInputError("Tell me what to search for.")
    return f'"{cleaned}"'


def _like_query(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _require_relative(relative: str) -> None:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise UserInputError("Invalid attachment path.")
