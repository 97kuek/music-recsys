"""共通評価、失敗を含む集計、設定と保存の再現性を確認する。"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_runner_matches_truth_by_id_instead_of_row_order():
    from dataclasses import replace

    from src.data import load_dataset
    from src.evaluation.benchmark import run_benchmark
    from src.recommenders.popularity import PopularityRecommender

    dataset = load_dataset(ROOT / "tests/fixtures/examples/toy/dataset.json", top_k=2)
    shuffled = replace(dataset, ground_truth=tuple(reversed(dataset.ground_truth)))
    report = run_benchmark(
        shuffled,
        PopularityRecommender(shuffled.train_interactions).recommend,
        model_name="popularity",
    )
    assert report.summary.recall == 0.625
    assert report.runs[0].evaluation.recall == 0.5


def test_all_empty_truth_has_undefined_macro_metrics():
    from dataclasses import replace

    from src.data import load_dataset
    from src.evaluation.benchmark import run_benchmark
    from src.recommenders.popularity import PopularityRecommender
    from src.types import GroundTruth

    dataset = load_dataset(ROOT / "tests/fixtures/examples/toy/dataset.json", top_k=2)
    dataset = replace(
        dataset,
        ground_truth=tuple(GroundTruth(request.request_id, ()) for request in dataset.requests),
    )
    report = run_benchmark(
        dataset,
        PopularityRecommender(dataset.train_interactions).recommend,
        model_name="popularity",
    )
    assert report.summary.ndcg is None
    assert report.summary.recall is None
    assert report.summary.n_evaluated == 0
    assert report.summary.n_excluded == 5


def test_runner_rejects_mixed_top_k():
    from dataclasses import replace

    from src.data import load_dataset
    from src.evaluation.benchmark import run_benchmark
    from src.recommenders.popularity import PopularityRecommender

    dataset = load_dataset(ROOT / "tests/fixtures/examples/toy/dataset.json", top_k=2)
    requests = (replace(dataset.requests[0], top_k=3), *dataset.requests[1:])
    dataset = replace(dataset, requests=requests)
    with pytest.raises(ValueError, match="top_k"):
        run_benchmark(
            dataset,
            PopularityRecommender(dataset.train_interactions).recommend,
            model_name="popularity",
        )


def test_runner_macro_average_excludes_only_requests_without_truth():
    from src.data import load_dataset
    from src.evaluation.benchmark import run_benchmark
    from src.recommenders.popularity import PopularityRecommender

    dataset = load_dataset(ROOT / "tests/fixtures/examples/toy/dataset.json", top_k=2)
    model = PopularityRecommender(dataset.train_interactions)
    report = run_benchmark(dataset, model.recommend, model_name="popularity", seed=42)
    assert report.summary.ndcg == pytest.approx(0.561019236584229)
    assert report.summary.recall == 0.625
    assert report.summary.n_requests == 5
    assert report.summary.n_evaluated == 4
    assert report.summary.n_excluded == 1
    assert report.summary.n_failed == 0
    assert report.runs[0].response.item_ids == ("track_c", "track_d")
    assert report.runs[-1].evaluation.ndcg is None
    assert report.metadata.dataset_sha256 == dataset.sha256
    assert report.metadata.seed == 42
    assert report.metadata.top_k == 2
    assert len(report.metadata.source_sha256) == 64


def test_runner_keeps_failed_requests_in_accuracy_denominator():
    from src.data import load_dataset
    from src.evaluation.benchmark import run_benchmark
    from src.recommenders.popularity import PopularityRecommender

    dataset = load_dataset(ROOT / "tests/fixtures/examples/toy/dataset.json", top_k=2)
    model = PopularityRecommender(dataset.train_interactions)

    def recommend(request):
        if request.request_id == "r1":
            raise RuntimeError("sensitive error contents must not be saved")
        return model.recommend(request)

    report = run_benchmark(dataset, recommend, model_name="failing")
    assert report.summary.recall == 0.5
    assert report.summary.n_evaluated == 4
    assert report.summary.n_failed == 1
    assert report.runs[0].response.error_type == "RuntimeError"
    assert report.runs[0].evaluation.ndcg == 0.0


def test_all_failed_requests_score_zero_and_are_not_dropped():
    from src.data import load_dataset
    from src.evaluation.benchmark import run_benchmark

    def recommend(request):
        raise RuntimeError("failure")

    dataset = load_dataset(ROOT / "tests/fixtures/examples/toy/dataset.json", top_k=2)
    report = run_benchmark(dataset, recommend, model_name="failing")
    assert report.summary.ndcg == 0.0
    assert report.summary.recall == 0.0
    assert report.summary.n_failed == 5
    assert report.summary.n_evaluated == 4


def test_wrong_response_request_id_is_recorded_as_failure():
    from src.data import load_dataset
    from src.evaluation.benchmark import run_benchmark
    from src.types import RecommendationResponse

    dataset = load_dataset(ROOT / "tests/fixtures/examples/toy/dataset.json", top_k=2)
    report = run_benchmark(
        dataset,
        lambda request: RecommendationResponse("wrong", ("track_a",)),
        model_name="wrong-id",
    )
    assert report.summary.n_failed == 5
    assert report.runs[0].response.request_id == "r1"
    assert report.runs[0].response.error_type == "ValueError"


def test_load_config_resolves_dataset_relative_to_config(tmp_path, monkeypatch):
    from src.evaluation.benchmark import load_config, run_experiment

    monkeypatch.chdir(tmp_path)
    config = load_config(ROOT / "tests/fixtures/configs/toy.toml")
    assert config.dataset_path == ROOT / "tests/fixtures/examples/toy/dataset.json"
    first = run_experiment(ROOT / "tests/fixtures/configs/toy.toml")
    second = run_experiment(ROOT / "tests/fixtures/configs/toy.toml")
    assert first.summary.ndcg == second.summary.ndcg
    assert first.summary.recall == second.summary.recall
    assert [run.response for run in first.runs] == [run.response for run in second.runs]
    assert first.metadata.config_sha256 == second.metadata.config_sha256


@pytest.mark.parametrize(
    "field, value", [("top_k", "0"), ("top_k", "true"), ("seed", '"42"'), ("model", '"missing"')]
)
def test_config_rejects_invalid_experiment_conditions(tmp_path, field, value):
    from src.evaluation.benchmark import load_config

    config = {"dataset": '"data.json"', "model": '"popularity"', "top_k": "2", "seed": "42"}
    config[field] = value
    path = tmp_path / "bad.toml"
    path.write_text("[benchmark]\n" + "\n".join(f"{key} = {item}" for key, item in config.items()))
    with pytest.raises(ValueError):
        load_config(path)


def test_save_report_keeps_existing_results_and_serializes_undefined_metrics(tmp_path):
    from src.evaluation.benchmark import run_experiment, save_report

    report = run_experiment(ROOT / "tests/fixtures/configs/toy.toml")
    first = save_report(report, tmp_path)
    first_contents = (first / "results.json").read_bytes()
    second = save_report(report, tmp_path)
    assert first != second
    assert (first / "results.json").read_bytes() == first_contents
    saved = json.loads((second / "results.json").read_text())
    assert saved["summary"]["recall"] == 0.625
    assert saved["runs"][-1]["evaluation"]["ndcg"] is None
    assert saved["runs"][0]["response"]["item_ids"] == ["track_c", "track_d"]
    assert (second / "summary.md").is_file()


def test_error_message_is_not_persisted(tmp_path):
    from src.data import load_dataset
    from src.evaluation.benchmark import run_benchmark, save_report

    def recommend(request):
        raise RuntimeError("sensitive error contents must not be saved")

    dataset = load_dataset(ROOT / "tests/fixtures/examples/toy/dataset.json", top_k=2)
    path = save_report(run_benchmark(dataset, recommend, model_name="failing"), tmp_path)
    contents = (path / "results.json").read_text()
    assert "sensitive error contents" not in contents
    assert "RuntimeError" in contents


def test_cli_runs_from_another_working_directory(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.evaluation.benchmark",
            "--config",
            str(ROOT / "tests/fixtures/configs/toy.toml"),
            "--output",
            str(tmp_path / "runs"),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Recall@2: 0.625000" in result.stdout
    files = list((tmp_path / "runs").glob("*/results.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text())["summary"]["n_evaluated"] == 4
