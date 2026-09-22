"""Tests for CLI commands using Typer CliRunner."""

import json
import pytest
from typer.testing import CliRunner

from topdown.cli import app
from topdown.storage.sqlite_backend import SQLiteBackend
from topdown.storage.models import Run, Sample

runner = CliRunner()


@pytest.fixture
def db_with_data(tmp_path):
    """Create a populated database and return its path."""
    db_path = tmp_path / "cli_test.db"
    backend = SQLiteBackend(db_path=db_path)
    backend.initialize()

    run = Run(
        run_id="cli-test-001",
        process_name="redis-server",
        level=2,
        labels={
            "git_branch": "unstable",
            "test_name": "set-get-100",
            "topology": "oss-standalone",
        },
    )
    backend.insert_run(run)

    samples = [
        Sample(run_id="cli-test-001", timestamp=1.0, metric_name="Frontend_Bound", value=15.0, unit="%"),
        Sample(run_id="cli-test-001", timestamp=1.0, metric_name="Backend_Bound", value=45.0, unit="%"),
        Sample(run_id="cli-test-001", timestamp=1.0, metric_name="Backend_Bound.Memory_Bound", value=30.0, unit="%"),
        Sample(run_id="cli-test-001", timestamp=1.0, metric_name="Backend_Bound.Memory_Bound.DRAM_Bound", value=18.0, unit="%"),
        Sample(run_id="cli-test-001", timestamp=1.0, metric_name="Backend_Bound.Core_Bound", value=15.0, unit="%"),
        Sample(run_id="cli-test-001", timestamp=1.0, metric_name="Bad_Speculation", value=10.0, unit="%"),
        Sample(run_id="cli-test-001", timestamp=1.0, metric_name="Retiring", value=30.0, unit="%"),
    ]
    backend.insert_samples(samples)
    backend.close()
    return str(db_path)


class TestVersionCommand:
    def test_version(self):
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert "topdown-profiler" in result.output


class TestListCommand:
    def test_list_no_data(self, tmp_path):
        db_path = str(tmp_path / "empty.db")
        result = runner.invoke(app, ["list", "--db", db_path, "--last", "24h"])
        assert result.exit_code == 0
        assert "No runs found" in result.output

    def test_list_with_data(self, db_with_data):
        result = runner.invoke(app, ["list", "--db", db_with_data, "--last", "24h"])
        assert result.exit_code == 0
        assert "cli-test-001" in result.output or "redis-server" in result.output

    def test_list_json(self, db_with_data):
        result = runner.invoke(app, ["list", "--db", db_with_data, "--json", "--last", "24h"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_list_filter_by_label(self, db_with_data):
        result = runner.invoke(app, ["list", "--db", db_with_data, "--label", "git_branch=unstable", "--last", "24h"])
        assert result.exit_code == 0
        assert "redis-server" in result.output

    def test_list_filter_by_label_no_match(self, db_with_data):
        result = runner.invoke(app, ["list", "--db", db_with_data, "--label", "git_branch=nonexistent", "--last", "24h"])
        assert result.exit_code == 0
        assert "No runs found" in result.output


class TestQueryCommand:
    def test_query_bottlenecks(self, db_with_data):
        result = runner.invoke(app, ["query", "--db", db_with_data, "--bottlenecks", "--last", "24h"])
        assert result.exit_code == 0
        assert "Backend_Bound" in result.output

    def test_query_tree(self, db_with_data):
        result = runner.invoke(app, ["query", "--db", db_with_data, "--tree", "--last", "24h"])
        assert result.exit_code == 0
        assert "Backend_Bound" in result.output

    def test_query_funnel(self, db_with_data):
        result = runner.invoke(app, ["query", "--db", db_with_data, "--funnel", "--last", "24h"])
        assert result.exit_code == 0
        assert "Funnel" in result.output or "Useful" in result.output

    def test_query_by_bottleneck(self, db_with_data):
        result = runner.invoke(app, ["query", "--db", db_with_data, "--bottleneck", "DRAM_Bound", "--min-pct", "10", "--last", "24h"])
        assert result.exit_code == 0
        # Rich table truncates column content; check for partial match
        assert "Bottleneck" in result.output or "cli-test" in result.output or "18.0" in result.output

    def test_query_json(self, db_with_data):
        # Use --bottleneck with --json to get parseable JSON
        result = runner.invoke(app, ["query", "--db", db_with_data, "--bottleneck", "DRAM_Bound", "--min-pct", "10", "--json", "--last", "24h"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)

    def test_query_by_run_id(self, db_with_data):
        result = runner.invoke(app, ["query", "--db", db_with_data, "--run-id", "cli-test-001", "--bottlenecks", "--last", "24h"])
        assert result.exit_code == 0

    def test_query_with_label_filter(self, db_with_data):
        result = runner.invoke(app, [
            "query", "--db", db_with_data,
            "--label", "git_branch=unstable",
            "--bottlenecks", "--last", "24h",
        ])
        assert result.exit_code == 0


class TestExplainCommand:
    def test_explain_metric(self):
        result = runner.invoke(app, ["explain", "Backend_Bound"])
        assert result.exit_code == 0
        assert "Backend" in result.output

    def test_explain_partial_name(self):
        result = runner.invoke(app, ["explain", "DRAM_Bound"])
        assert result.exit_code == 0
        assert "DRAM" in result.output or "memory" in result.output.lower()

    def test_explain_unknown(self):
        result = runner.invoke(app, ["explain", "FakeMetric123"])
        assert result.exit_code == 1

    def test_explain_json(self):
        result = runner.invoke(app, ["explain", "Frontend_Bound", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "description" in data


class TestCompareCommand:
    def test_compare_requires_args(self):
        result = runner.invoke(app, ["compare"])
        # Should fail without run IDs or labels
        assert result.exit_code != 0 or "Error" in result.output

    def test_compare_nonexistent_run(self, db_with_data):
        result = runner.invoke(app, ["compare", "nonexistent-a", "nonexistent-b", "--db", db_with_data])
        assert result.exit_code == 1 or "not found" in result.output


class TestInstallServiceCommand:
    def test_preview(self):
        result = runner.invoke(app, [
            "install-service", "--process", "redis-server",
            "--level", "2", "--every", "5m", "--preview",
        ])
        assert result.exit_code == 0
        assert "ExecStart" in result.output
        assert "redis-server" in result.output
        assert "systemd" in result.output.lower() or "[Unit]" in result.output


def test_uprof_process_request_fails_before_collection_or_storage(tmp_path, monkeypatch):
    from topdown.config import TopdownConfig

    monkeypatch.setattr("topdown.cli.get_config", lambda path: TopdownConfig(
        db_path=tmp_path / "unused.db", collector="uprof_pcm"))
    monkeypatch.setattr("topdown.collector.toplev.check_perf_permissions", lambda: (True, "ok"))
    monkeypatch.setattr("topdown.collector.uprof_pcm.check_uprof_pcm_available", lambda path: (True, "ok"))
    monkeypatch.setattr("topdown.collector.process_resolver.resolve_pids", lambda process: [1234])
    monkeypatch.setattr("topdown.cli.collect_auto_labels", lambda *args, **kwargs: {})
    monkeypatch.setattr("topdown.cli.get_backend", lambda *args: pytest.fail("must not store a run"))
    monkeypatch.setattr("topdown.collector.uprof_pcm.subprocess.Popen",
                        lambda *args, **kwargs: pytest.fail("must not launch system-wide collection"))
    result = runner.invoke(app, ["collect", "--process", "redis-server", "--duration", "1s"])
    assert result.exit_code == 1
    assert "only supports explicit system-wide" in result.output
    assert not (tmp_path / "unused.db").exists()
