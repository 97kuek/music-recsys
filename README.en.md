# music-recsys

Music recommendation research with Python 3.12, uv, Parquet, GPU evaluation, and a React/FastAPI listening app

The standard workflow is `lab_data → Parquet → CLI evaluation → saved-result notebooks`
Docker, sudo, and a resident database are not required for offline research

## Documentation

- [Overview, commands, sources, and retention](README.md)
- [Architecture and recommendation pipeline](docs/architecture.md)
- [Offline and online evaluation](docs/evaluation.md)
- [Participant UI and Mac operations](docs/ui.md)
- [Open tasks and decisions](docs/todo.md)
- [Results, figures, and limitations](docs/discussion.md)

The five Markdown files in `docs/` each stay within 200 lines
Exact metrics, source hashes, and historical processing versions remain in their immutable experiment artifacts

## Evaluation boundaries

- Include every learning-observed user, report K=2–20 and all four evaluation aspects
- Separate new discovery and repeat listening, Validation exploration and final Test
- Never pass future observations to recommendation models or choose parameters from Test maxima
- Keep unmeasured values null and distinguish measured zero
- Check the host GPU before long runs; retain backend, solver, precision, seed, and timings
- Choose the same experiment JSON explicitly in CLI and notebooks

Use [the overview](README.md) for setup and commands, and [UI operations](docs/ui.md) for the listening app

## License

Original code and documentation are [MIT licensed](LICENSE); see [NOTICE](NOTICE)
Datasets, audio, models, and participant logs retain their own conditions
