import os
import re
from dataclasses import dataclass
from pathlib import Path

TOKEN_RE = re.compile(r"^\d{8,12}:[A-Za-z0-9_-]{30,}$")
_BARE_TOKEN_RE = re.compile(r"^(\d{8,12})([A-Za-z0-9_-]{30,})$")


@dataclass(frozen=True)
class Config:
    token: str
    allowed_user_ids: frozenset[int]
    data_dir: Path
    database_path: Path
    storage_dir: Path
    exports_dir: Path
    backup_dir: Path
    poll_seconds: int


def normalize_token(raw: str) -> str:
    cleaned = raw.strip().strip('"').strip("'")
    if ":" in cleaned:
        return cleaned
    match = _BARE_TOKEN_RE.fullmatch(cleaned)
    if match:
        return f"{match.group(1)}:{match.group(2)}"
    return cleaned


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def resolve_data_dir() -> Path:
    raw = os.environ.get("DATA_DIR") or os.environ.get("data_dir")
    if raw:
        path = Path(raw)
    else:
        fade = Path("/data")
        try:
            writable = fade.is_dir() and os.access(fade, os.W_OK)
        except OSError:
            writable = False
        path = fade / "timecapsule" if writable else Path("data")
    path.mkdir(parents=True, exist_ok=True)
    for name in ("storage", "exports", "backups"):
        (path / name).mkdir(exist_ok=True)
    return path


def load_config(env_file: Path | None = Path(".env")) -> Config:
    if env_file is not None:
        load_env_file(env_file)
    token = normalize_token(
        os.environ.get("BOT_TOKEN")
        or os.environ.get("TELEGRAM_BOT_TOKEN")
        or os.environ.get("bot_token")
        or ""
    )
    if not TOKEN_RE.fullmatch(token):
        raise SystemExit("Set BOT_TOKEN to the token from BotFather.")
    allowed: set[int] = set()
    for key in ("ADMIN_ID", "admin_id"):
        raw = os.environ.get(key, "").strip()
        if raw:
            allowed.add(_parse_user_id(raw, key))
    extra = os.environ.get("ALLOWED_USER_IDS", "")
    for part in extra.split(","):
        if part.strip():
            allowed.add(_parse_user_id(part.strip(), "ALLOWED_USER_IDS"))
    try:
        poll_seconds = int(os.environ.get("POLL_SECONDS", "30"))
    except ValueError:
        poll_seconds = 30
    if poll_seconds < 5:
        poll_seconds = 30
    data_dir = resolve_data_dir()
    return Config(
        token=token,
        allowed_user_ids=frozenset(allowed),
        data_dir=data_dir,
        database_path=data_dir / "capsule.db",
        storage_dir=data_dir / "storage",
        exports_dir=data_dir / "exports",
        backup_dir=data_dir / "backups",
        poll_seconds=poll_seconds,
    )


def _parse_user_id(raw: str, key: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise SystemExit(f"{key} must be a numeric Telegram user id.") from exc
