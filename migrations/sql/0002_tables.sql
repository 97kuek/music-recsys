CREATE TABLE datasets (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    key text NOT NULL UNIQUE CHECK (key !~ '^[[:space:]]*$'),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE dataset_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id uuid NOT NULL REFERENCES datasets(id) ON DELETE RESTRICT,
    version_label text NOT NULL CHECK (version_label !~ '^[[:space:]]*$'),
    source_ref text NOT NULL CHECK (source_ref !~ '^[[:space:]]*$'),
    source_manifest jsonb NOT NULL CHECK (recsys_valid_source_manifest(source_manifest)),
    manifest_sha256 text NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$')
        CHECK (manifest_sha256 = recsys_json_sha256(source_manifest)),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (dataset_id, id),
    UNIQUE (dataset_id, source_ref, manifest_sha256)
);

CREATE TABLE data_artifacts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id uuid NOT NULL REFERENCES datasets(id) ON DELETE RESTRICT,
    format text NOT NULL CHECK (format !~ '^[[:space:]]*$'),
    storage_key text NOT NULL UNIQUE CHECK (
        storage_key !~ '^[[:space:]]*$' AND storage_key !~ '^/' AND
        storage_key !~ '(^|/)\.{1,2}(/|$)' AND storage_key !~ '//|/$' AND
        position(chr(92) IN storage_key) = 0
    ),
    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    byte_size bigint NOT NULL CHECK (byte_size >= 0),
    row_count bigint CHECK (row_count >= 0),
    verified_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (format <> 'parquet' OR row_count IS NOT NULL),
    UNIQUE (dataset_id, id),
    UNIQUE (dataset_id, sha256)
);

CREATE TABLE source_files (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id uuid NOT NULL REFERENCES datasets(id) ON DELETE RESTRICT,
    dataset_version_id uuid NOT NULL,
    logical_name text NOT NULL CHECK (logical_name !~ '^[[:space:]]*$'),
    source_url text NOT NULL CHECK (source_url !~ '^[[:space:]]*$'),
    expected_checksum_algorithm text,
    expected_checksum text,
    artifact_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((expected_checksum_algorithm IS NULL AND expected_checksum IS NULL) OR
           (expected_checksum_algorithm IS NOT NULL AND expected_checksum IS NOT NULL)),
    FOREIGN KEY (dataset_id, dataset_version_id)
        REFERENCES dataset_versions(dataset_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (dataset_id, artifact_id)
        REFERENCES data_artifacts(dataset_id, id) ON DELETE RESTRICT,
    UNIQUE (dataset_id, dataset_version_id, id),
    UNIQUE (dataset_version_id, logical_name)
);

CREATE TABLE tracks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id uuid NOT NULL REFERENCES datasets(id) ON DELETE RESTRICT,
    source_id text NOT NULL CHECK (source_id !~ '^[[:space:]]*$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (dataset_id, id),
    UNIQUE (dataset_id, source_id)
);

CREATE TABLE users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id uuid NOT NULL REFERENCES datasets(id) ON DELETE RESTRICT,
    source_id text NOT NULL CHECK (source_id !~ '^[[:space:]]*$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (dataset_id, id),
    UNIQUE (dataset_id, source_id)
);

CREATE TABLE track_metadata (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id uuid NOT NULL REFERENCES datasets(id) ON DELETE RESTRICT,
    track_id uuid NOT NULL,
    payload jsonb NOT NULL CHECK (recsys_valid_tags(payload)),
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$')
        CHECK (content_sha256 = recsys_json_sha256(payload)),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (dataset_id, track_id) REFERENCES tracks(dataset_id, id) ON DELETE RESTRICT,
    UNIQUE (dataset_id, track_id, id),
    UNIQUE (track_id, content_sha256)
);

CREATE TABLE import_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id uuid NOT NULL REFERENCES datasets(id) ON DELETE RESTRICT,
    dataset_version_id uuid NOT NULL,
    adapter_name text NOT NULL CHECK (adapter_name !~ '^[[:space:]]*$'),
    adapter_version text NOT NULL CHECK (adapter_version !~ '^[[:space:]]*$'),
    pipeline_version text NOT NULL CHECK (pipeline_version !~ '^[[:space:]]*$'),
    dependency_lock_sha256 text NOT NULL CHECK (dependency_lock_sha256 ~ '^[0-9a-f]{64}$'),
    config jsonb NOT NULL CHECK (jsonb_typeof(config) = 'object'),
    input_fingerprint text NOT NULL CHECK (input_fingerprint ~ '^[0-9a-f]{64}$'),
    input_sealed_at timestamptz,
    status text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'succeeded', 'failed')),
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    result_snapshot_id uuid,
    stats jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(stats) = 'object'),
    error_type text,
    CHECK (input_sealed_at IS NULL OR input_sealed_at >= started_at),
    CHECK (finished_at IS NULL OR finished_at >= started_at),
    CHECK ((status = 'running' AND finished_at IS NULL AND result_snapshot_id IS NULL
            AND error_type IS NULL) OR
           (status = 'succeeded' AND finished_at IS NOT NULL AND result_snapshot_id IS NOT NULL
            AND error_type IS NULL) OR
           (status = 'failed' AND finished_at IS NOT NULL AND result_snapshot_id IS NULL
            AND error_type IS NOT NULL AND error_type !~ '^[[:space:]]*$')),
    FOREIGN KEY (dataset_id, dataset_version_id)
        REFERENCES dataset_versions(dataset_id, id) ON DELETE RESTRICT,
    UNIQUE (dataset_id, dataset_version_id, id)
);

CREATE TABLE import_run_sources (
    dataset_id uuid NOT NULL REFERENCES datasets(id) ON DELETE RESTRICT,
    dataset_version_id uuid NOT NULL,
    import_run_id uuid NOT NULL,
    source_file_id uuid NOT NULL,
    PRIMARY KEY (import_run_id, source_file_id),
    FOREIGN KEY (dataset_id, dataset_version_id, import_run_id)
        REFERENCES import_runs(dataset_id, dataset_version_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (dataset_id, dataset_version_id, source_file_id)
        REFERENCES source_files(dataset_id, dataset_version_id, id) ON DELETE RESTRICT
);

CREATE TABLE metadata_snapshots (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id uuid NOT NULL REFERENCES datasets(id) ON DELETE RESTRICT,
    dataset_version_id uuid NOT NULL,
    created_by_import_run_id uuid NOT NULL UNIQUE,
    input_fingerprint text NOT NULL CHECK (input_fingerprint ~ '^[0-9a-f]{64}$'),
    content_fingerprint text NOT NULL CHECK (content_fingerprint ~ '^[0-9a-f]{64}$'),
    manifest jsonb NOT NULL CHECK (jsonb_typeof(manifest) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    CHECK (published_at IS NULL OR published_at >= created_at),
    FOREIGN KEY (dataset_id, dataset_version_id)
        REFERENCES dataset_versions(dataset_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (dataset_id, dataset_version_id, created_by_import_run_id)
        REFERENCES import_runs(dataset_id, dataset_version_id, id) ON DELETE RESTRICT,
    UNIQUE (dataset_id, id),
    UNIQUE (dataset_id, dataset_version_id, id),
    UNIQUE (dataset_version_id, input_fingerprint)
);

ALTER TABLE import_runs ADD CONSTRAINT fk_import_result
    FOREIGN KEY (dataset_id, dataset_version_id, result_snapshot_id)
    REFERENCES metadata_snapshots(dataset_id, dataset_version_id, id) ON DELETE RESTRICT;

CREATE TABLE snapshot_tracks (
    dataset_id uuid NOT NULL REFERENCES datasets(id) ON DELETE RESTRICT,
    snapshot_id uuid NOT NULL,
    track_id uuid NOT NULL,
    track_metadata_id uuid,
    PRIMARY KEY (snapshot_id, track_id),
    FOREIGN KEY (dataset_id, snapshot_id)
        REFERENCES metadata_snapshots(dataset_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (dataset_id, track_id) REFERENCES tracks(dataset_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (dataset_id, track_id, track_metadata_id)
        REFERENCES track_metadata(dataset_id, track_id, id) ON DELETE RESTRICT
);

CREATE TABLE snapshot_users (
    dataset_id uuid NOT NULL REFERENCES datasets(id) ON DELETE RESTRICT,
    snapshot_id uuid NOT NULL,
    user_id uuid NOT NULL,
    PRIMARY KEY (snapshot_id, user_id),
    FOREIGN KEY (dataset_id, snapshot_id)
        REFERENCES metadata_snapshots(dataset_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (dataset_id, user_id) REFERENCES users(dataset_id, id) ON DELETE RESTRICT
);

CREATE TABLE snapshot_artifacts (
    dataset_id uuid NOT NULL REFERENCES datasets(id) ON DELETE RESTRICT,
    snapshot_id uuid NOT NULL,
    role text NOT NULL CHECK (role IN ('tracks', 'users')),
    artifact_id uuid NOT NULL,
    PRIMARY KEY (snapshot_id, role),
    FOREIGN KEY (dataset_id, snapshot_id)
        REFERENCES metadata_snapshots(dataset_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (dataset_id, artifact_id)
        REFERENCES data_artifacts(dataset_id, id) ON DELETE RESTRICT
);

-- 台帳からの逆引き・FK参照を支える。snapshot先頭のPKだけでは足りない箇所。
CREATE INDEX ix_snapshot_tracks_track ON snapshot_tracks (dataset_id, track_id);
CREATE INDEX ix_snapshot_tracks_metadata ON snapshot_tracks (dataset_id, track_id, track_metadata_id);
CREATE INDEX ix_snapshot_users_user ON snapshot_users (dataset_id, user_id);
CREATE INDEX ix_snapshot_artifacts_artifact ON snapshot_artifacts (dataset_id, artifact_id);
CREATE INDEX ix_source_files_artifact ON source_files (dataset_id, artifact_id);
CREATE INDEX ix_import_sources_file ON import_run_sources (dataset_id, dataset_version_id, source_file_id);
CREATE INDEX ix_import_runs_result ON import_runs (dataset_id, dataset_version_id, result_snapshot_id);
