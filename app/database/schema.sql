PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS settings (
    user_id INTEGER PRIMARY KEY,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    daily_limit INTEGER NOT NULL DEFAULT 1,
    discovery_enabled INTEGER NOT NULL DEFAULT 1,
    delivery_time TEXT NOT NULL DEFAULT '09:00',
    last_discovery_date TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_counters (
    user_id INTEGER PRIMARY KEY,
    next_number INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS capsules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('note', 'url', 'forward')),
    text_content TEXT,
    url TEXT,
    file_path TEXT,
    file_name TEXT,
    created_at TEXT NOT NULL,
    unlock_at TEXT,
    release_mode TEXT NOT NULL CHECK (release_mode IN ('open', 'sealed')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active')),
    source_chat_id INTEGER,
    source_message_id INTEGER,
    UNIQUE (user_id, public_id)
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    UNIQUE (user_id, name)
);

CREATE TABLE IF NOT EXISTS capsule_tags (
    capsule_id INTEGER NOT NULL REFERENCES capsules(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (capsule_id, tag_id)
);

CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    capsule_id INTEGER NOT NULL REFERENCES capsules(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL,
    delivered_at TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('scheduled', 'rediscovery')),
    response TEXT CHECK (
        response IS NULL OR response IN ('useful', 'completed', 'outdated', 'still_relevant')
    ),
    state TEXT NOT NULL CHECK (state IN ('pending', 'sent', 'failed')),
    telegram_message_id INTEGER,
    responded_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_capsules_user_created
    ON capsules(user_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_capsules_unlock
    ON capsules(status, unlock_at);
CREATE INDEX IF NOT EXISTS idx_deliveries_capsule
    ON deliveries(capsule_id, kind, state, delivered_at);
CREATE INDEX IF NOT EXISTS idx_deliveries_user_day
    ON deliveries(user_id, kind, state, delivered_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_capsules_source
    ON capsules(user_id, source_chat_id, source_message_id)
    WHERE source_chat_id IS NOT NULL AND source_message_id IS NOT NULL;

CREATE VIRTUAL TABLE IF NOT EXISTS capsules_fts USING fts5(
    public_id,
    text_content,
    url,
    content='capsules',
    content_rowid='id',
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS capsules_ai AFTER INSERT ON capsules BEGIN
    INSERT INTO capsules_fts(rowid, public_id, text_content, url)
    VALUES (
        new.id,
        new.public_id,
        coalesce(new.text_content, ''),
        coalesce(new.url, '')
    );
END;

CREATE TRIGGER IF NOT EXISTS capsules_ad AFTER DELETE ON capsules BEGIN
    INSERT INTO capsules_fts(capsules_fts, rowid, public_id, text_content, url)
    VALUES (
        'delete',
        old.id,
        old.public_id,
        coalesce(old.text_content, ''),
        coalesce(old.url, '')
    );
END;

CREATE TRIGGER IF NOT EXISTS capsules_au AFTER UPDATE ON capsules BEGIN
    INSERT INTO capsules_fts(capsules_fts, rowid, public_id, text_content, url)
    VALUES (
        'delete',
        old.id,
        old.public_id,
        coalesce(old.text_content, ''),
        coalesce(old.url, '')
    );
    INSERT INTO capsules_fts(rowid, public_id, text_content, url)
    VALUES (
        new.id,
        new.public_id,
        coalesce(new.text_content, ''),
        coalesce(new.url, '')
    );
END;
