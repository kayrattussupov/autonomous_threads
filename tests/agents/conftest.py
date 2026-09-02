import pytest


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    """Override parent conftest's DB fixture - agent tests don't need the database."""
    yield
