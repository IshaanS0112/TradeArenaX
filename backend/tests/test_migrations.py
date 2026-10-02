"""Migrations must produce exactly the schema the models declare."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from app.db.session import Base

alembic = pytest.importorskip("alembic", reason="alembic is a dev/deploy dependency")
from alembic.config import Config  # noqa: E402
from alembic import command  # noqa: E402

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _shape(engine) -> dict[str, tuple[dict[str, str], list[tuple[str, ...]]]]:
    """Columns and indexes per table, ignoring alembic's own bookkeeping."""
    inspector = inspect(engine)
    return {
        table: (
            {c["name"]: str(c["type"]).upper() for c in inspector.get_columns(table)},
            sorted(tuple(i["column_names"]) for i in inspector.get_indexes(table)),
        )
        for table in inspector.get_table_names()
        if table != "alembic_version"
    }


@pytest.fixture
def migrated_url(tmp_path):
    # env.py honours a URL already set on the Config, so each test migrates its own throwaway file.
    return f"sqlite:///{tmp_path / 'migrated.db'}"


def test_upgrade_head_reproduces_the_model_schema(tmp_path, migrated_url):
    """`alembic upgrade head` on an empty database == `create_all`."""
    command.upgrade(_alembic_config(migrated_url), "head")

    created_url = f"sqlite:///{tmp_path / 'created.db'}"
    created_engine = create_engine(created_url)
    import app.models  # noqa: F401  (register every mapper)

    Base.metadata.create_all(created_engine)

    migrated = _shape(create_engine(migrated_url))
    created = _shape(created_engine)

    assert set(migrated) == set(created), "migrations and models disagree on tables"
    for table in sorted(created):
        assert migrated[table] == created[table], f"{table} differs after migration"


def test_downgrade_to_baseline_and_back(migrated_url):
    """The chain runs both ways."""
    config = _alembic_config(migrated_url)
    command.upgrade(config, "head")
    head = _shape(create_engine(migrated_url))

    command.downgrade(config, "-1")
    rolled_back = _shape(create_engine(migrated_url))
    assert "ensembles" not in rolled_back
    assert "latency_config" not in rolled_back["agents"][0]

    command.upgrade(config, "head")
    assert _shape(create_engine(migrated_url)) == head


def test_every_revision_has_a_down_revision_chain():
    """No orphan revisions: exactly one head, one base, linear in between."""
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_alembic_config("sqlite://"))
    heads = script.get_heads()
    assert len(heads) == 1, f"expected a single head, found {heads}"
    revisions = list(script.walk_revisions())
    assert revisions, "no revisions found"
    assert revisions[-1].down_revision is None, "the base revision must have no parent"


def test_alembic_ini_does_not_hardcode_a_database_url():
    """The URL comes from settings, so a migration cannot hit the wrong database."""
    text = (BACKEND_ROOT / "alembic.ini").read_text()
    for line in text.splitlines():
        if line.strip().startswith("sqlalchemy.url"):
            assert line.split("=", 1)[1].strip() == ""
            break
    else:  # pragma: no cover - the option is always present in the template
        pytest.fail("alembic.ini has no sqlalchemy.url option")
    assert os.environ.get("DATABASE_URL"), "the suite runs against a temp SQLite file"
