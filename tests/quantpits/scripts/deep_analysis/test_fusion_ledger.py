"""
Unit tests for the Fusion Run Ledger feature.

Covers:
  1. append_to_fusion_ledger() — correct serialization, is_oos flag, append mode
  2. EnsembleEvolutionAgent._load_performance_history() — ledger primary, run_metadata fallback, dedup
  3. EnsembleEvolutionAgent._load_oos_history() — OOS trend, IS→OOS decay calculation
  4. EnsembleEvolutionAgent._load_combo_trends() — ledger supplement for CSV gaps
"""

import importlib
import json
import os
import sys
import pytest
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock


@pytest.fixture(autouse=False)
def mock_env(tmp_path, monkeypatch):
    """
    Set up a hermetic environment for tests that import ensemble_fusion.py.

    ensemble_fusion.py captures ROOT_DIR at module-import time from env.ROOT_DIR.
    We must:
      1. Set the env var BEFORE env.py raises RuntimeError.
      2. Reload env.py so ROOT_DIR is updated.
      3. Patch the already-captured ROOT_DIR inside ensemble_fusion module.
    """
    workspace = tmp_path / "ew"
    workspace.mkdir()
    (workspace / "data").mkdir()

    monkeypatch.setenv("QLIB_WORKSPACE_DIR", str(workspace))

    # Reload env so ROOT_DIR picks up the new env var
    import quantpits.utils.env as env_mod
    importlib.reload(env_mod)

    # Also patch ROOT_DIR inside ensemble_fusion if it has already been imported
    if "quantpits.scripts.ensemble_fusion" in sys.modules:
        monkeypatch.setattr(sys.modules["quantpits.scripts.ensemble_fusion"],
                            "ROOT_DIR", str(workspace))

    yield workspace

    # monkeypatch already restores QLIB_WORKSPACE_DIR; no manual teardown needed.
    # Re-reloading env here would fail because the env var is already gone.

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ledger_record(
    run_date="2026-03-01",
    combo_name="combo_A",
    models=None,
    method="equal",
    is_default=True,
    only_last_years=0,
    only_last_months=0,
    annualized_return=0.15,
    annualized_excess=0.08,
    max_drawdown=-0.12,
    calmar=1.25,
    information_ratio=1.1,
):
    models = models or ["gru", "linear_Alpha158"]
    is_oos = only_last_years > 0 or only_last_months > 0
    return {
        "run_date": run_date,
        "combo_name": combo_name,
        "models": models,
        "method": method,
        "is_default": is_default,
        "eval_window": {
            "start": "2020-01-01",
            "end": run_date,
            "is_oos": is_oos,
            "only_last_years": only_last_years,
            "only_last_months": only_last_months,
        },
        "metrics": {
            "annualized_return": annualized_return,
            "annualized_excess": annualized_excess,
            "max_drawdown": max_drawdown,
            "calmar": calmar,
            "information_ratio": information_ratio,
        },
        "sub_model_metrics": {},
        "loo_contributions": {"gru": {"delta": 0.003}, "linear_Alpha158": {"delta": 0.001}},
        "source": "ensemble_fusion",
        "cli_args": "--from-config",
    }


def _write_ledger(path, records):
    """Write a list of dicts as JSONL."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


# ---------------------------------------------------------------------------
# 1. append_to_fusion_ledger()
# ---------------------------------------------------------------------------

class TestAppendToFusionLedger:
    """Tests for the ledger append function in ensemble_fusion.py."""

    def _call(self, workspace_root, **kwargs):
        """
        Call append_to_fusion_ledger directly, patching ROOT_DIR so the
        import-time os.chdir and env.ROOT_DIR don't fail.
        """
        import quantpits.scripts.ensemble_fusion as ef_mod
        defaults = dict(
            workspace_root=str(workspace_root),
            run_date="2026-04-01",
            combo_name="combo_A",
            models=["gru", "linear"],
            method="equal",
            is_default=True,
            eval_window={"start": "2020-01-01", "end": "2026-04-01",
                         "only_last_years": 0, "only_last_months": 0},
            metrics={"annualized_return": 0.15, "calmar": 1.2},
        )
        defaults.update(kwargs)
        ef_mod.append_to_fusion_ledger(**defaults)

    def test_creates_ledger_file(self, mock_env):
        self._call(mock_env)
        ledger = mock_env / "data" / "fusion_run_ledger.jsonl"
        assert ledger.exists()

    def test_record_content(self, mock_env):
        self._call(mock_env, combo_name="my_combo",
                   models=["gru", "alstm"],
                   eval_window={"start": "2024-01-01", "end": "2026-04-01",
                                "only_last_years": 1, "only_last_months": 0})
        ledger = mock_env / "data" / "fusion_run_ledger.jsonl"
        records = [json.loads(l) for l in ledger.read_text().splitlines()]
        assert len(records) == 1
        r = records[0]
        assert r["combo_name"] == "my_combo"
        assert r["models"] == ["gru", "alstm"]
        assert r["eval_window"]["is_oos"] is True
        assert r["source"] == "ensemble_fusion"

    def test_full_run_not_oos(self, mock_env):
        """A run without --only-last-years/months should be marked is_oos=False."""
        self._call(mock_env)
        ledger = mock_env / "data" / "fusion_run_ledger.jsonl"
        r = json.loads(ledger.read_text().splitlines()[0])
        assert r["eval_window"]["is_oos"] is False

    def test_append_mode_preserves_history(self, mock_env):
        """Multiple calls must append, not overwrite."""
        self._call(mock_env, run_date="2026-03-01")
        self._call(mock_env, run_date="2026-04-01")
        ledger = mock_env / "data" / "fusion_run_ledger.jsonl"
        lines = [l for l in ledger.read_text().splitlines() if l.strip()]
        assert len(lines) == 2
        dates = [json.loads(l)["run_date"] for l in lines]
        assert "2026-03-01" in dates
        assert "2026-04-01" in dates

    def test_no_loo_contributions_still_writes(self, mock_env):
        self._call(mock_env, loo_contributions=None)
        ledger = mock_env / "data" / "fusion_run_ledger.jsonl"
        r = json.loads(ledger.read_text().splitlines()[0])
        assert r["loo_contributions"] == {}

    def test_cli_args_recorded(self, mock_env):
        self._call(mock_env, cli_args=["--from-config", "--freq", "week"])
        ledger = mock_env / "data" / "fusion_run_ledger.jsonl"
        r = json.loads(ledger.read_text().splitlines()[0])
        assert "--from-config" in r["cli_args"]

    def test_creates_data_dir_if_missing(self, mock_env):
        """data/ dir should be created automatically."""
        import shutil
        shutil.rmtree(mock_env / "data")
        self._call(mock_env)
        assert (mock_env / "data" / "fusion_run_ledger.jsonl").exists()



# ---------------------------------------------------------------------------
# 2. EnsembleEvolutionAgent._load_performance_history()
# ---------------------------------------------------------------------------

class TestLoadPerformanceHistory:
    """Tests for the unified history loader that merges ledger + run_metadata."""

    @pytest.fixture
    def agent(self):
        from quantpits.scripts.deep_analysis.agents.ensemble_eval import EnsembleEvolutionAgent
        return EnsembleEvolutionAgent()

    def _ctx(self, workspace_root, mock_analysis_context=None):
        """Build a minimal AnalysisContext pointing at workspace_root."""
        from quantpits.scripts.deep_analysis.base_agent import AnalysisContext
        return AnalysisContext(
            start_date="2026-01-01",
            end_date="2026-12-31",
            window_label="test",
            workspace_root=workspace_root,
        )

    def test_reads_ledger_as_primary(self, tmp_path, agent):
        records = [
            _make_ledger_record("2026-03-01"),
            _make_ledger_record("2026-04-01"),
        ]
        _write_ledger(str(tmp_path / "data" / "fusion_run_ledger.jsonl"), records)
        ctx = self._ctx(str(tmp_path))
        hist = agent._load_performance_history(ctx)
        assert len(hist) == 2
        assert all(r["source"] == "fusion_ledger" for r in hist)

    def test_chronological_order(self, tmp_path, agent):
        records = [
            _make_ledger_record("2026-04-01"),
            _make_ledger_record("2026-02-01"),
            _make_ledger_record("2026-03-01"),
        ]
        _write_ledger(str(tmp_path / "data" / "fusion_run_ledger.jsonl"), records)
        ctx = self._ctx(str(tmp_path))
        hist = agent._load_performance_history(ctx)
        dates = [r["date"] for r in hist]
        assert dates == sorted(dates)

    def test_fallback_to_run_metadata(self, tmp_path, agent):
        """When no ledger exists, should fall back to run_metadata.json."""
        ensemble_dir = tmp_path / "output" / "ensemble" / "run1"
        ensemble_dir.mkdir(parents=True)
        metadata = {
            "combo_name": "combo_bf",
            "oos_metrics": {"oos_calmar": 2.5, "oos_excess_return": 0.1},
            "oos_start_date": "2025-01-01",
            "oos_end_date": "2025-12-31",
        }
        (ensemble_dir / "run_metadata.json").write_text(json.dumps(metadata))
        # Rename file to embed a date (coordinator logic)
        os.rename(
            ensemble_dir / "run_metadata.json",
            ensemble_dir / "run_metadata.json"
        )
        # Simulate a directory name with a date so _extract_date finds it
        dated_dir = tmp_path / "output" / "ensemble" / "brute_force_2026-03-20"
        dated_dir.mkdir(parents=True)
        (dated_dir / "run_metadata.json").write_text(json.dumps(metadata))

        ctx = self._ctx(str(tmp_path))
        hist = agent._load_performance_history(ctx)
        # Should have at least the brute_force entry
        assert len(hist) >= 1
        assert any(r["source"] == "run_metadata" for r in hist)

    def test_deduplication_ledger_wins(self, tmp_path, agent):
        """Same (date, combo) pair: ledger entry must win over run_metadata."""
        ledger_rec = _make_ledger_record("2026-03-20", combo_name="combo_A")
        _write_ledger(str(tmp_path / "data" / "fusion_run_ledger.jsonl"), [ledger_rec])

        dated_dir = tmp_path / "output" / "ensemble" / "brute_force_2026-03-20"
        dated_dir.mkdir(parents=True)
        metadata = {
            "combo_name": "combo_A",
            "oos_metrics": {"oos_calmar": 9.9},
        }
        (dated_dir / "run_metadata.json").write_text(json.dumps(metadata))

        ctx = self._ctx(str(tmp_path))
        hist = agent._load_performance_history(ctx)

        # Only one record for (2026-03-20, combo_A)
        matches = [r for r in hist if r["date"] == "2026-03-20" and r["combo"] == "combo_A"]
        assert len(matches) == 1
        assert matches[0]["source"] == "fusion_ledger"
        # Must NOT have the bogus calmar=9.9 from run_metadata
        assert matches[0].get("calmar") != 9.9

    def test_empty_ledger_file_handled(self, tmp_path, agent):
        """Empty / corrupt ledger should not raise."""
        ledger_path = tmp_path / "data" / "fusion_run_ledger.jsonl"
        ledger_path.parent.mkdir(parents=True)
        ledger_path.write_text("\n\n{bad json\n\n")
        ctx = self._ctx(str(tmp_path))
        hist = agent._load_performance_history(ctx)
        assert isinstance(hist, list)

    def test_no_data_returns_empty_list(self, tmp_path, agent):
        ctx = self._ctx(str(tmp_path))
        hist = agent._load_performance_history(ctx)
        assert hist == []


# ---------------------------------------------------------------------------
# 3. EnsembleEvolutionAgent._load_oos_history()
# ---------------------------------------------------------------------------

class TestLoadOosHistory:
    """Tests for the OOS trend analysis that now uses the ledger as primary source."""

    @pytest.fixture
    def agent(self):
        from quantpits.scripts.deep_analysis.agents.ensemble_eval import EnsembleEvolutionAgent
        return EnsembleEvolutionAgent()

    def _ctx(self, workspace_root):
        from quantpits.scripts.deep_analysis.base_agent import AnalysisContext
        return AnalysisContext(
            start_date="2026-01-01",
            end_date="2026-12-31",
            window_label="test",
            workspace_root=workspace_root,
        )

    def test_oos_trend_from_ledger(self, tmp_path, agent):
        records = [
            _make_ledger_record("2026-02-01", only_last_years=1, calmar=2.0),
            _make_ledger_record("2026-03-01", only_last_years=1, calmar=1.8),
            _make_ledger_record("2026-04-01", only_last_years=1, calmar=1.5),
        ]
        _write_ledger(str(tmp_path / "data" / "fusion_run_ledger.jsonl"), records)
        ctx = self._ctx(str(tmp_path))
        result = agent._load_oos_history(ctx)
        assert result.get("oos_runs") == 3
        assert result.get("latest_oos_calmar") == pytest.approx(1.5)
        assert result.get("best_oos_calmar") == pytest.approx(2.0)
        # Slope should be negative (degrading)
        assert result.get("oos_calmar_slope", 0) < 0

    def test_no_oos_runs_reports_note(self, tmp_path, agent):
        """When all runs are full (not OOS), should return a helpful note."""
        records = [_make_ledger_record("2026-03-01")]  # no only_last_years
        _write_ledger(str(tmp_path / "data" / "fusion_run_ledger.jsonl"), records)
        ctx = self._ctx(str(tmp_path))
        result = agent._load_oos_history(ctx)
        assert result.get("oos_runs") == 0
        assert "note" in result

    def test_is_oos_decay_computed(self, tmp_path, agent):
        """When combo has both full and OOS snapshots, decay ratio should appear."""
        records = [
            # Full run (calmar=2.0)
            _make_ledger_record("2026-04-01", combo_name="combo_A", calmar=2.0,
                                only_last_years=0),
            # OOS run (calmar=1.2) => decay = (1.2-2.0)/2.0 = -0.4
            _make_ledger_record("2026-04-02", combo_name="combo_A", calmar=1.2,
                                only_last_years=1),
        ]
        _write_ledger(str(tmp_path / "data" / "fusion_run_ledger.jsonl"), records)
        ctx = self._ctx(str(tmp_path))
        result = agent._load_oos_history(ctx)
        decays = result.get("is_oos_decay", [])
        assert len(decays) >= 1
        combo_decay = next(d for d in decays if d["combo"] == "combo_A")
        assert combo_decay["decay_ratio"] == pytest.approx(-0.4, abs=0.01)

    def test_no_data_returns_empty(self, tmp_path, agent):
        ctx = self._ctx(str(tmp_path))
        result = agent._load_oos_history(ctx)
        assert result == {}

    def test_history_limited_to_last_5(self, tmp_path, agent):
        records = [
            _make_ledger_record(f"2026-0{i+1}-01", only_last_years=1, calmar=float(i))
            for i in range(1, 8)  # 7 OOS records
        ]
        _write_ledger(str(tmp_path / "data" / "fusion_run_ledger.jsonl"), records)
        ctx = self._ctx(str(tmp_path))
        result = agent._load_oos_history(ctx)
        assert len(result.get("history", [])) == 5


# ---------------------------------------------------------------------------
# 4. EnsembleEvolutionAgent._load_combo_trends()
# ---------------------------------------------------------------------------

class TestLoadComboTrends:
    """Tests for combo trend loading that now uses ledger as primary source."""

    @pytest.fixture
    def agent(self):
        from quantpits.scripts.deep_analysis.agents.ensemble_eval import EnsembleEvolutionAgent
        return EnsembleEvolutionAgent()

    def _ctx(self, workspace_root, combo_comparison_files=None):
        from quantpits.scripts.deep_analysis.base_agent import AnalysisContext
        return AnalysisContext(
            start_date="2026-01-01",
            end_date="2026-12-31",
            window_label="test",
            workspace_root=workspace_root,
            combo_comparison_files=combo_comparison_files or [],
        )

    def test_loads_combos_from_ledger(self, tmp_path, agent):
        records = [
            _make_ledger_record("2026-02-01", combo_name="combo_A"),
            _make_ledger_record("2026-03-01", combo_name="combo_A"),
            _make_ledger_record("2026-02-01", combo_name="combo_B"),
        ]
        _write_ledger(str(tmp_path / "data" / "fusion_run_ledger.jsonl"), records)
        ctx = self._ctx(str(tmp_path))
        trends = agent._load_combo_trends(ctx)
        assert "combo_A" in trends
        assert "combo_B" in trends
        assert len(trends["combo_A"]) == 2

    def test_chronological_sort(self, tmp_path, agent):
        records = [
            _make_ledger_record("2026-03-01", combo_name="combo_A"),
            _make_ledger_record("2026-01-01", combo_name="combo_A"),
        ]
        _write_ledger(str(tmp_path / "data" / "fusion_run_ledger.jsonl"), records)
        ctx = self._ctx(str(tmp_path))
        trends = agent._load_combo_trends(ctx)
        dates = [e["_date"] for e in trends["combo_A"]]
        assert dates == sorted(dates)

    def test_csv_supplements_when_no_overlap(self, tmp_path, agent):
        """CSV entry on a different date should be added without being deduplicated."""
        ledger_records = [_make_ledger_record("2026-02-01", combo_name="combo_A")]
        _write_ledger(str(tmp_path / "data" / "fusion_run_ledger.jsonl"), ledger_records)

        # CSV with a different date
        csv_path = tmp_path / "combo_comparison_2026-03-20.csv"
        pd.DataFrame({
            "combo": ["combo_A"],
            "total_return": [0.20],
            "calmar_ratio": [3.0],
        }).to_csv(csv_path, index=False)

        ctx = self._ctx(str(tmp_path), combo_comparison_files=[str(csv_path)])
        trends = agent._load_combo_trends(ctx)
        assert len(trends["combo_A"]) == 2  # ledger entry + CSV entry

    def test_csv_deduped_if_same_date(self, tmp_path, agent):
        """CSV entry on the same date as ledger entry must be skipped."""
        ledger_records = [_make_ledger_record("2026-03-20", combo_name="combo_A")]
        _write_ledger(str(tmp_path / "data" / "fusion_run_ledger.jsonl"), ledger_records)

        csv_path = tmp_path / "combo_comparison_2026-03-20.csv"
        pd.DataFrame({
            "combo": ["combo_A"],
            "total_return": [0.99],
        }).to_csv(csv_path, index=False)

        ctx = self._ctx(str(tmp_path), combo_comparison_files=[str(csv_path)])
        trends = agent._load_combo_trends(ctx)
        # Only one entry (from ledger, not the bogus CSV one)
        assert len(trends["combo_A"]) == 1
        assert trends["combo_A"][0]["_source"] == "ledger"

    def test_window_filter_applied(self, tmp_path, agent):
        """Records outside ctx.start_date/end_date must be excluded."""
        records = [
            _make_ledger_record("2025-12-01", combo_name="combo_A"),  # before window
            _make_ledger_record("2026-03-01", combo_name="combo_A"),  # inside
        ]
        _write_ledger(str(tmp_path / "data" / "fusion_run_ledger.jsonl"), records)
        from quantpits.scripts.deep_analysis.base_agent import AnalysisContext
        ctx = AnalysisContext(
            start_date="2026-01-01", end_date="2026-12-31",
            window_label="test", workspace_root=str(tmp_path),
        )
        trends = agent._load_combo_trends(ctx)
        assert len(trends.get("combo_A", [])) == 1
        assert trends["combo_A"][0]["_date"] == "2026-03-01"
