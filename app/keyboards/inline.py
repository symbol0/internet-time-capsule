from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.formatting import RESPONSE_CODES, RESPONSE_LABELS
from app.models import Capsule
from app.timeutils import utcnow

PAGE_SIZE = 5

TIMEZONES = (
    "UTC",
    "Asia/Dhaka",
    "Asia/Kolkata",
    "Europe/London",
    "Europe/Berlin",
    "America/New_York",
)


def _button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def markup(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def start_keyboard() -> InlineKeyboardMarkup:
    return markup(
        [
            [_button("Save a note", "go:save"), _button("Set timezone", "set:tz:menu")],
        ]
    )


def draft_keyboard() -> InlineKeyboardMarkup:
    return markup([[_button("Save", "draft:save"), _button("Don't save", "draft:drop")]])


def schedule_keyboard(capsule_id: int) -> InlineKeyboardMarkup:
    return markup(
        [
            [
                _button("Tomorrow", f"seal:{capsule_id}:1d"),
                _button("In 30 days", f"seal:{capsule_id}:30d"),
            ],
            [
                _button("In 1 year", f"seal:{capsule_id}:1y"),
                _button("Pick a date", f"seal:{capsule_id}:pick"),
            ],
            [_button("Keep available", f"seal:{capsule_id}:open")],
        ]
    )


def review_keyboard(delivery_id: int) -> InlineKeyboardMarkup:
    labels = (
        ("c", "Started it"),
        ("s", "Still interested"),
        ("u", "Useful"),
        ("o", "Not relevant"),
    )
    return markup(
        [
            [_button(label, f"rv:{delivery_id}:{code}") for code, label in labels[:2]],
            [_button(label, f"rv:{delivery_id}:{code}") for code, label in labels[2:]],
        ]
    )


def vault_keyboard(capsules: list[Capsule], offset: int, total: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    for capsule in capsules:
        pair.append(_button(capsule.public_id, f"item:{capsule.id}"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    nav: list[InlineKeyboardButton] = []
    if offset > 0:
        nav.append(_button("Newer", f"vault:{max(0, offset - PAGE_SIZE)}"))
    if offset + len(capsules) < total:
        nav.append(_button("Older", f"vault:{offset + PAGE_SIZE}"))
    if nav:
        rows.append(nav)
    return markup(rows or [[_button("Refresh", "vault:0")]])


def item_keyboard(capsule: Capsule, now=None) -> InlineKeyboardMarkup:
    from app.formatting import is_locked

    moment = now or utcnow()
    if is_locked(capsule, moment):
        first = [_button("Unseal", f"unseal:{capsule.id}")]
    else:
        first = [_button("Seal", f"sealmenu:{capsule.id}")]
    return markup(
        [
            first,
            [_button("Add tag", f"tag:{capsule.id}"), _button("Delete", f"del:{capsule.id}")],
            [_button("Back to vault", "vault:0")],
        ]
    )


def confirm_keyboard(yes: str, no: str, yes_label: str = "Delete") -> InlineKeyboardMarkup:
    return markup([[_button(yes_label, yes), _button("Cancel", no)]])


def tags_keyboard(tags) -> InlineKeyboardMarkup:
    rows = []
    for tag in tags[:20]:
        rows.append(
            [
                _button(f"#{tag.name}", f"tagview:{tag.id}"),
                _button("Remove", f"tagx:{tag.id}"),
            ]
        )
    return markup(rows or [[_button("Vault", "vault:0")]])


def settings_keyboard(settings) -> InlineKeyboardMarkup:
    disc = "off" if settings.discovery_enabled else "on"
    toggle = "0" if settings.discovery_enabled else "1"
    return markup(
        [
            [_button(f"Turn rediscovery {disc}", f"set:disc:{toggle}")],
            [
                _button("1 a day", "set:limit:1"),
                _button("3 a day", "set:limit:3"),
                _button("5 a day", "set:limit:5"),
            ],
            [
                _button("08:00", "set:time:08:00"),
                _button("09:00", "set:time:09:00"),
                _button("21:00", "set:time:21:00"),
            ],
            [_button("Timezone", "set:tz:menu")],
        ]
    )


def timezone_keyboard() -> InlineKeyboardMarkup:
    rows = [[_button(name, f"set:tz:{name}")] for name in TIMEZONES]
    rows.append([_button("Other", "set:tz:ask")])
    return markup(rows)


def response_label(code: str) -> str:
    response = RESPONSE_CODES[code]
    return RESPONSE_LABELS[response]
