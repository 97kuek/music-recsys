# music-recsys

- [日本語](README.md)
- A research repository for comparing music recommenders and future LLM/Agent strategy selection
- Python 3.12, uv, and Parquet for offline evaluation; React, TypeScript, and FastAPI for the listening MVP
- A research-only LLM/Agent rehearsal foundation is available; participant UI and live API calls remain disconnected. Music4All ID mapping and web preview permissions require verification

- Backend and UI separate domain rules, use cases, adapters and presentation; tests enforce dependency direction

## Start

```bash
uv sync --locked --group data --group models --group notebook --group tracking
.venv/bin/python -m src.interfaces.cli.lab_data
.venv/bin/python -m src.interfaces.cli.full_evaluation --config configs/popularity-plays.json
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
| Optional Decisions, LangGraph and LangSmith adapters; synthetic rehearsal | [LLM/Agent foundation](docs/llm-agent-evaluation.md) |
| Phase 2–4 responsibilities, contract reuse, and missing components | [Recommendation extension design](docs/recommendation-design.md) |
| Five-person exploratory assignment, researcher analysis, CSV/JSON exports | [Online comparison protocol](docs/online-comparison.md) |
| Modules, Notebook/CLI execution, outputs, and development | [Code and execution workflow](docs/experiment-workflow.md) |
| Data sources, checksums, and delivery status | [Data sources](docs/data-sources.md) |
| UI/API, storage capacity, Mac Docker, and Tunnel/Access | [Application architecture](docs/app-architecture.md) |
| Events, consent, storage, administration, deletion, and participant comparisons | [Online evaluation](docs/online-study.md) |
| Online formulas, numerators/denominators, missing data, and analysis CLI | [Online metrics](docs/online-metrics.md) |
| Listening, optional list evaluation, and future Reactive Agent screens | [Online UI/UX](docs/online-ux.md) |
| Returning participants, preference consent, review, edits, and deletion | [Persistent preferences](docs/participant-profile.md) |
| Candidates, ground truth, Recall/NDCG, diversity, efficiency, and missing values | [Offline evaluation metrics](docs/listening-evaluation.md) |
| Four phases: standalone, multi-stage, LLM selection, Reactive Agent; open decisions | [Research plan](docs/research-plan.md) |
| Existing observations and hypotheses | [Discussion](docs/discussion.md) |

## Notebooks

| Notebook | Purpose |
| --- | --- |
| [00_lab_start](notebooks/00_lab_start.ipynb) | Input readiness, status, and explicit evaluation launch |
| [01_data](notebooks/01_data.ipynb) | Temporal splits, population, and listening distributions |
| [02_analysis](notebooks/02_analysis.ipynb) | Saved results across four evaluation aspects; explicit selection/freezing |
| [03_recommendation](notebooks/03_recommendation.ipynb) | Inspect recommendations and explanations for selected small-scale conditions |

## Application and data protection

- The lab server trains and evaluates offline; one Mac Docker container serves React and FastAPI, without Pages or Workers
- Extract the Mac ZIP and run `bash deploy/mac.sh start` for local use; remote use requires `start-remote` and Cloudflare Tunnel/Access on the Mac host
- Training data and evaluation results stay in the lab; source archives, serving models, selected track metadata/audio, and participant logs belong on the Mac; transfer is pending
- See [online evaluation](docs/online-study.md) for consent, retention, and resuming; see [application architecture](docs/app-architecture.md) for storage budgets and transfer
- Do not expose lab servers or open public ports; verify authenticated remote access on the Mac
- Raw data, audio, models, individual results, logs, and credentials stay outside Git; follow [AGENTS.md](AGENTS.md)

## Architecture diagrams

- Standalone SVG diagrams distinguish current behavior from future proposals; open an image to zoom
- The diagrams are labeled in Japanese; see the [interactive HTML](docs/architecture.html) for node details and [documentation](#documentation) for Mermaid diagrams and specifications; frameworks and fusion methods shown are not adopted decisions

### UI, API, database, and deployment

![Participants connect through Cloudflare Access and Tunnel to React, FastAPI, recommenders, and SQLite on the Mac](docs/diagrams/app.svg)

### Research batches and Notebooks

![lab_data produces Parquet; the CLI trains models and evaluates Validation; Notebooks inspect saved results](docs/diagrams/batch.svg)

### Future LLM and Agent orchestration

![Fixed selection, one-shot Decisions selection, and a Reactive Agent triggered by an explicit user request; execution frameworks remain undecided](docs/diagrams/agent.svg)

### Future multi-stage recommendation

![Proposed shared candidate constraints, retrieval, fusion, reranking, permissions, and presentation; methods and composition remain undecided](docs/diagrams/ranking.svg)

## License

- Original code and documentation use the [MIT License](LICENSE); see [NOTICE](NOTICE) for scope and third-party material
- Datasets, audio, images, trained artifacts, and participant logs are excluded and remain subject to their own terms
