# music-recsys

- [日本語](README.md)
- A research repository for comparing music recommenders and future LLM/Agent strategy selection
- Python 3.12, uv, and Parquet for offline evaluation; React, TypeScript, and FastAPI for the listening MVP
- LLM/Agent selection is not implemented; Music4All audio access and listening permissions are pending; the public UI uses synthetic demo tracks

## Start

```bash
uv sync --locked --group data --group models --group notebook --group tracking
.venv/bin/python -m music_recsys.lab_data
.venv/bin/python -m music_recsys.full_evaluation --config configs/popularity-plays.json
.venv/bin/jupyter lab --no-browser --ip=127.0.0.1 --port=8888
```

- Standard lab workflow: `lab_data → Parquet → CLI evaluation → Notebook inspection`; Docker, sudo, and a persistent database are optional
- Explicitly select and load the same experiment JSON in each Notebook; nothing is selected by default
- Compare all training-observed users at K=2–20 across accuracy, sustained preference, diversity/discovery, and efficiency
- Keep Validation comparison separate from final Test evaluation; do not automatically adopt a model or primary metric
- Check host-side `nvidia-smi` before training and prefer an available GPU for supported workloads

## Documentation

| Topic | Document, currently in Japanese |
| --- | --- |
| Recommenders, shared API, and future LLM/Agent orchestration | [Recommendation architecture](docs/architecture.md) |
| Modules, Notebook/CLI execution, outputs, and development | [Code and execution workflow](docs/experiment-workflow.md) |
| UI/API, catalog/models, Mac Docker, and the public demo | [Application architecture](docs/app-architecture.md) |
| Events, consent, storage, administration, deletion, and participant comparisons | [Online evaluation](docs/online-study.md) |
| Candidates, ground truth, Recall/NDCG, diversity, efficiency, and missing values | [Offline evaluation metrics](docs/listening-evaluation.md) |
| Late-November interim and late-January final reports | [Research plan](docs/research-plan.md) |
| Existing observations and hypotheses | [Discussion](docs/discussion.md) |

## Notebooks

| Notebook | Purpose |
| --- | --- |
| [00_lab_start](notebooks/00_lab_start.ipynb) | Input readiness, status, and explicit evaluation launch |
| [01_data](notebooks/01_data.ipynb) | Temporal splits, population, and listening distributions |
| [02_analysis](notebooks/02_analysis.ipynb) | Saved results across four evaluation aspects; explicit selection/freezing |
| [03_recommendation](notebooks/03_recommendation.ipynb) | Inspect recommendations and explanations for selected small-scale conditions |

## Application and data protection

- [Public synthetic demo](https://musicrecsys.pages.dev), separate from real-data models and licensed-track listening
- For the Mac distribution, extract the ZIP and run `bash deploy/mac.sh start`; Docker Desktop is required
- With optional consent, development logs are retained for 24 hours in `~/music-recsys/state/sessions.sqlite`
- Do not expose lab servers or open public ports; authenticated remote access from a personal Mac requires separate setup
- Raw data, audio, models, individual results, logs, and credentials stay outside Git; follow [AGENTS.md](AGENTS.md)

## License

- Original code and documentation use the [MIT License](LICENSE); see [NOTICE](NOTICE) for scope and third-party material
- Datasets, audio, images, trained artifacts, and participant logs are excluded and remain subject to their own terms
