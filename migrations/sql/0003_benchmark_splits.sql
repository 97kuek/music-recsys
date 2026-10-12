-- 固定role列を1行で確定し、公開後のrole追加を含む変更を拒否する。
CREATE TABLE benchmark_splits (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id uuid NOT NULL REFERENCES datasets(id) ON DELETE RESTRICT,
    dataset_version_id uuid NOT NULL,
    snapshot_id uuid NOT NULL,
    config jsonb NOT NULL CHECK (jsonb_typeof(config) = 'object'),
    summary jsonb NOT NULL CHECK (jsonb_typeof(summary) = 'object'),
    artifact_manifest jsonb NOT NULL CHECK (jsonb_typeof(artifact_manifest) = 'object'),
    content_fingerprint text NOT NULL UNIQUE CHECK (content_fingerprint ~ '^[0-9a-f]{64}$'),
    events_artifact_id uuid NOT NULL,
    train_artifact_id uuid NOT NULL,
    validation_artifact_id uuid NOT NULL,
    test_artifact_id uuid NOT NULL,
    validation_benchmark_artifact_id uuid,
    test_benchmark_artifact_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (dataset_id, dataset_version_id, snapshot_id)
        REFERENCES metadata_snapshots(dataset_id, dataset_version_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (dataset_id, events_artifact_id)
        REFERENCES data_artifacts(dataset_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (dataset_id, train_artifact_id)
        REFERENCES data_artifacts(dataset_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (dataset_id, validation_artifact_id)
        REFERENCES data_artifacts(dataset_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (dataset_id, test_artifact_id)
        REFERENCES data_artifacts(dataset_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (dataset_id, validation_benchmark_artifact_id)
        REFERENCES data_artifacts(dataset_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (dataset_id, test_benchmark_artifact_id)
        REFERENCES data_artifacts(dataset_id, id) ON DELETE RESTRICT,
    CHECK (content_fingerprint = recsys_json_sha256(jsonb_build_object(
        'snapshot_id', snapshot_id::text, 'config', config,
        'artifacts', artifact_manifest, 'summary', summary)))
);

CREATE INDEX ix_benchmark_splits_snapshot
    ON benchmark_splits(dataset_id, dataset_version_id, snapshot_id);

CREATE FUNCTION recsys_validate_split() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE actual jsonb; invalid_format boolean;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM metadata_snapshots
                   WHERE id = NEW.snapshot_id AND published_at IS NOT NULL) THEN
        RAISE EXCEPTION 'split requires a published snapshot' USING ERRCODE = '23514';
    END IF;
    SELECT jsonb_object_agg(role, jsonb_build_object(
               'sha256', a.sha256, 'format', a.format,
               'byte_size', a.byte_size, 'row_count', a.row_count)),
           bool_or(a.format <> expected_format)
      INTO actual, invalid_format
      FROM (VALUES
          ('events', NEW.events_artifact_id, 'parquet'),
          ('train', NEW.train_artifact_id, 'parquet'),
          ('validation', NEW.validation_artifact_id, 'parquet'),
          ('test', NEW.test_artifact_id, 'parquet'),
          ('validation_benchmark', NEW.validation_benchmark_artifact_id, 'json'),
          ('test_benchmark', NEW.test_benchmark_artifact_id, 'json')
      ) AS roles(role, artifact_id, expected_format)
      JOIN data_artifacts a ON a.id = roles.artifact_id AND a.dataset_id = NEW.dataset_id;
    IF invalid_format OR actual IS DISTINCT FROM NEW.artifact_manifest THEN
        RAISE EXCEPTION 'split artifact manifest or format mismatch' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER validate_split BEFORE INSERT ON benchmark_splits
    FOR EACH ROW EXECUTE FUNCTION recsys_validate_split();
CREATE TRIGGER immutable_split BEFORE UPDATE OR DELETE ON benchmark_splits
    FOR EACH ROW EXECUTE FUNCTION recsys_reject_mutation();
CREATE TRIGGER immutable_split_truncate BEFORE TRUNCATE ON benchmark_splits
    FOR EACH STATEMENT EXECUTE FUNCTION recsys_reject_mutation();
