import pytest
from compile_cache import caching_compile_check, prune

from orchestrator.checks import compile_check, local_tests, solutions_run
from orchestrator.checks.test_script_check import SKIP_CHECK_ENV
from orchestrator.steps import base


def pytest_addoption(parser):
    parser.addoption(
        "--fast",
        action="store_true",
        help="не запускать тесты с маркером slow (сборка и запуск C++/Java)",
    )


def pytest_collection_modifyitems(config, items):
    """`--fast` отбирает тесты сам, а не через `-m "not slow"`: свой `-m`
    заменил бы `-m 'not integration'` из addopts и включил integration-тесты."""
    if not config.getoption("--fast"):
        return
    slow = [item for item in items if item.get_closest_marker("slow")]
    if slow:
        config.hook.pytest_deselected(items=slow)
        items[:] = [item for item in items if not item.get_closest_marker("slow")]


@pytest.fixture(autouse=True)
def _skip_test_script_check(monkeypatch):
    """Проверка test-script'а перед загрузкой (`upload_test_script`) запускает
    java и может скачивать FreeMarker — в тестах выключена; тесты самой
    проверки включают её обратно (`monkeypatch.delenv`)."""
    monkeypatch.setenv(SKIP_CHECK_ENV, "1")


# Сколько дней хранить в кэше сборку, которую ни один тест не запрашивал.
_COMPILE_CACHE_MAX_AGE_DAYS = 14


@pytest.fixture(scope="session")
def _compile_cache_dir(pytestconfig, tmp_path_factory):
    """Каталог кэша сборок: в `.pytest_cache` (без cacheprovider — временный,
    на сессию)."""
    cache = getattr(pytestconfig, "cache", None)
    if cache is None:
        yield tmp_path_factory.mktemp("compile-cache")
        return
    cache_dir = cache.mkdir("compiles")
    yield cache_dir
    prune(cache_dir, _COMPILE_CACHE_MAX_AGE_DAYS)


@pytest.fixture(autouse=True)
def _cached_compile_check(monkeypatch, _compile_cache_dir):
    """Сборки кода оркестратора (шаги, `local run`, `local check --full`)
    кэшируются между запусками, см. `compile_cache.py`. Тесты, которые сами
    импортируют `compile_check`, компилируют честно; образцы из `examples/`
    помечают удачные сборки в `.pytest_cache` по тому же ключу и между
    запусками пересобирают только изменившееся."""
    cached = caching_compile_check(compile_check.compile_check, _compile_cache_dir)
    for module in (base, local_tests, solutions_run):
        monkeypatch.setattr(module, "compile_check", cached)


@pytest.fixture
def problems_dir(tmp_path):
    return tmp_path / "problems"
