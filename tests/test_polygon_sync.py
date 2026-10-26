from unittest.mock import MagicMock

from orchestrator.polygon.sync import (
    INTERACTOR_FILE_KEY,
    fetch_remote_stamps,
    generator_source_names,
    remote_stamps,
)


def _source(name: str) -> dict:
    return {"name": name, "modificationTimeSeconds": 100, "length": 1}


def test_generator_source_names_skip_interactor():
    sources = [_source(n) for n in ("gen.cpp", "interactor.cpp", "inter.cpp", "val.cpp")]

    assert generator_source_names(sources, "val.cpp", None) == ["gen.cpp", "inter.cpp"]
    assert generator_source_names(sources, "val.cpp", None, "inter.cpp") == ["gen.cpp"]


def test_remote_stamps_include_assigned_interactor():
    files = {"sourceFiles": [_source("inter.cpp"), _source("gen.cpp")]}

    stamps = remote_stamps(files, [], None, None, "inter.cpp")

    assert stamps[INTERACTOR_FILE_KEY] == "inter.cpp:100:1"
    assert "generators/inter.cpp" not in stamps


def test_fetch_remote_stamps_asks_for_interactor_only_when_interactive():
    client = MagicMock()
    client.call.return_value = None

    fetch_remote_stamps(client, 1)
    assert "problem.interactor" not in [c.args[0] for c in client.call.call_args_list]

    fetch_remote_stamps(client, 1, interactive=True)
    assert "problem.interactor" in [c.args[0] for c in client.call.call_args_list]
