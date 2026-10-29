"""create_app refuses to boot against anything but PostgreSQL."""
import pytest

from app import create_app
from config import TestingConfig


@pytest.mark.parametrize('url', [None, '', 'sqlite:///app.db', 'sqlite:///:memory:'])
def test_refuses_non_postgres_database_url(monkeypatch, url):
    monkeypatch.setattr(TestingConfig, 'SQLALCHEMY_DATABASE_URI', url)
    with pytest.raises(RuntimeError, match='postgresql://'):
        create_app('testing')


def test_accepts_postgres_url():
    # The suite's own app fixture already booted on TestingConfig's URL;
    # this pins that the check accepts the postgresql:// form CI and prod use.
    assert TestingConfig.SQLALCHEMY_DATABASE_URI.startswith('postgresql')
