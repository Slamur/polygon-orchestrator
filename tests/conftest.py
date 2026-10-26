import pytest

from orchestrator.checks.test_script_check import SKIP_CHECK_ENV


@pytest.fixture(autouse=True)
def _skip_test_script_check(monkeypatch):
    """Проверка test-script'а перед загрузкой (`upload_test_script`) запускает
    java и может скачивать FreeMarker — в тестах выключена; тесты самой
    проверки включают её обратно (`monkeypatch.delenv`)."""
    monkeypatch.setenv(SKIP_CHECK_ENV, "1")
