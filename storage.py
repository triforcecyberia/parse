"""
Персистентное хранилище "уже показанных" компаний, привязанное к
Telegram-аккаунту (user_id). Живёт в SQLite-файле рядом с ботом — переживает
перезапуски процесса. У каждого пользователя своя история: то, что уже
показали аккаунту 1, никак не влияет на то, что увидит аккаунт 2.
"""

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

DB_PATH = Path(__file__).parent / "seen_companies.db"


def _dedup_key(place) -> str:
    """
    Стабильный ключ компании: источник + название + адрес (в нижнем регистре,
    без пробелов по краям). Не используем id из API, т.к. Яндекс его не
    гарантирует в ответе — этого набора полей достаточно, чтобы не путать
    разные компании и при этом узнавать одну и ту же при повторном поиске.
    """
    raw = f"{place.source}|{place.name.strip().lower()}|{place.address.strip().lower()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _connect():
    return sqlite3.connect(DB_PATH)


def init_db() -> None:
    with closing(_connect()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS seen_companies (
                user_id INTEGER NOT NULL,
                company_key TEXT NOT NULL,
                seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, company_key)
            )
            """
        )
        conn.commit()


def filter_unseen(user_id: int, places: list) -> list:
    """Возвращает только те places, которых этот user_id ещё не видел."""
    if not places:
        return []
    keys = [_dedup_key(p) for p in places]
    with closing(_connect()) as conn:
        placeholders = ",".join("?" for _ in keys)
        rows = conn.execute(
            f"SELECT company_key FROM seen_companies "
            f"WHERE user_id = ? AND company_key IN ({placeholders})",
            [user_id, *keys],
        ).fetchall()
    seen_keys = {r[0] for r in rows}
    return [p for p, k in zip(places, keys) if k not in seen_keys]


def mark_seen(user_id: int, places: list) -> None:
    """Запоминает, что эти places уже показаны этому user_id."""
    if not places:
        return
    with closing(_connect()) as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO seen_companies (user_id, company_key) VALUES (?, ?)",
            [(user_id, _dedup_key(p)) for p in places],
        )
        conn.commit()


def reset_user(user_id: int) -> int:
    """Стирает историю показанных компаний для пользователя. Возвращает кол-во удалённых записей."""
    with closing(_connect()) as conn:
        cur = conn.execute("DELETE FROM seen_companies WHERE user_id = ?", (user_id,))
        conn.commit()
        return cur.rowcount


init_db()
