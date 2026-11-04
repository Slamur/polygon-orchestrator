import pytest
from compile_cache import caching_compile_check, prune

from orchestrator.checks import compile_check, local_tests, solutions_run
from orchestrator.checks.test_script_check import SKIP_CHECK_ENV
from orchestrator.steps import base


def pytest_addoption(parser):
    parser.addoption(
        "--fast",
        action="store_true",
        help="skip the tests marked slow (building and running C++/Java)",
    )


def pytest_collection_modifyitems(config, items):
    """`--fast` selects the tests itself, not via `-m "not slow"`: its own `-m`
    would replace `-m 'not integration'` from addopts and enable the integration tests."""
    if not config.getoption("--fast"):
        return
    slow = [item for item in items if item.get_closest_marker("slow")]
    if slow:
        config.hook.pytest_deselected(items=slow)
        items[:] = [item for item in items if not item.get_closest_marker("slow")]


@pytest.fixture(autouse=True)
def _skip_test_script_check(monkeypatch):
    """The test-script check before uploading (`upload_test_script`) runs
    java and may download FreeMarker — it's off in tests; the tests of the check
    itself turn it back on (`monkeypatch.delenv`)."""
    monkeypatch.setenv(SKIP_CHECK_ENV, "1")


# How many days to keep a build in the cache that no test requested.
_COMPILE_CACHE_MAX_AGE_DAYS = 14


@pytest.fixture(scope="session")
def _compile_cache_dir(pytestconfig, tmp_path_factory):
    """The build cache directory: in `.pytest_cache` (without cacheprovider — temporary,
    per session)."""
    cache = getattr(pytestconfig, "cache", None)
    if cache is None:
        yield tmp_path_factory.mktemp("compile-cache")
        return
    cache_dir = cache.mkdir("compiles")
    yield cache_dir
    prune(cache_dir, _COMPILE_CACHE_MAX_AGE_DAYS)


@pytest.fixture(autouse=True)
def _cached_compile_check(monkeypatch, _compile_cache_dir):
    """Builds of the orchestrator's code (steps, `local run`, `local check --full`)
    are cached between runs, see `compile_cache.py`. Tests that import
    `compile_check` themselves compile honestly; the samples from `examples/`
    mark successful builds in `.pytest_cache` by the same key and between
    runs rebuild only what changed."""
    cached = caching_compile_check(compile_check.compile_check, _compile_cache_dir)
    for module in (base, local_tests, solutions_run):
        monkeypatch.setattr(module, "compile_check", cached)


@pytest.fixture
def problems_dir(tmp_path):
    return tmp_path / "problems"
