import pytest
from compile_cache import caching_compile_check

from orchestrator.checks import compile_check, local_tests, solutions_run
from orchestrator.checks.test_script_check import SKIP_CHECK_ENV
from orchestrator.steps import base


@pytest.fixture(autouse=True)
def _skip_test_script_check(monkeypatch):
    """Проверка test-script'а перед загрузкой (`upload_test_script`) запускает
    java и может скачивать FreeMarker — в тестах выключена; тесты самой
    проверки включают её обратно (`monkeypatch.delenv`)."""
    monkeypatch.setenv(SKIP_CHECK_ENV, "1")


@pytest.fixture(scope="session")
def _compile_cache_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("compile-cache")


@pytest.fixture(autouse=True)
def _cached_compile_check(monkeypatch, _compile_cache_dir):
    """Сборки кода оркестратора (шаги, `local run`, `local check --full`)
    кэшируются на сессию, см. `compile_cache.py`. Тесты, которые сами
    импортируют `compile_check` (compile-check, образцы из `examples/`),
    компилируют честно."""
    cached = caching_compile_check(compile_check.compile_check, _compile_cache_dir)
    for module in (base, local_tests, solutions_run):
        monkeypatch.setattr(module, "compile_check", cached)


@pytest.fixture
def problems_dir(tmp_path):
    return tmp_path / "problems"
