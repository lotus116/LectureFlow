"""Summaries and cloud notes were removed from the confirmed product design.

Existing SQLite tables may remain on disk for local libraries; no API, export
prompt, or network summary path is offered. Prefer transcript exports instead.
"""

from __future__ import annotations


def migrate(db) -> None:
    # Keep historical tables so opening an older library does not fail migration.
    db.execute("""CREATE TABLE IF NOT EXISTS summaries (
        id TEXT PRIMARY KEY, lecture_id TEXT NOT NULL REFERENCES lectures(id),
        revision INTEGER NOT NULL,
        source_version TEXT, endpoint TEXT NOT NULL, model TEXT NOT NULL,
        materials TEXT NOT NULL,
        state TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0,
        output TEXT NOT NULL DEFAULT '', error TEXT, created_at TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS summary_chunks (
        summary_id TEXT NOT NULL REFERENCES summaries(id), digest TEXT NOT NULL,
        output TEXT NOT NULL, PRIMARY KEY(summary_id,digest))""")


def _removed() -> None:
    raise ValueError("笔记与云端总结已移出产品范围；请使用转写导出")


def preview(*_args, **_kwargs):
    _removed()


def export_prompt(*_args, **_kwargs):
    _removed()


def listing(*_args, **_kwargs):
    _removed()


def review(*_args, **_kwargs):
    _removed()


def create(*_args, **_kwargs):
    _removed()


def run(*_args, **_kwargs):
    _removed()
