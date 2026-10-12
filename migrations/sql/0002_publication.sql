-- 入力hashの対象にはDB採番・実行時刻・保存ルートを含めない。
CREATE FUNCTION recsys_input_document(run_id uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE AS $$
DECLARE result jsonb;
BEGIN
    SELECT jsonb_build_object(
        'dataset', d.key,
        'dataset_version', jsonb_build_object('source_ref', v.source_ref,
            'manifest_sha256', v.manifest_sha256),
        'files', coalesce((SELECT jsonb_agg(jsonb_build_object('name', f.logical_name,
            'sha256', a.sha256) ORDER BY f.logical_name COLLATE "C")
            FROM import_run_sources rs JOIN source_files f ON f.id = rs.source_file_id
            JOIN data_artifacts a ON a.id = f.artifact_id WHERE rs.import_run_id = r.id), '[]'::jsonb),
        'adapter', jsonb_build_object('name', r.adapter_name, 'version', r.adapter_version),
        'pipeline_version', r.pipeline_version,
        'dependency_lock_sha256', r.dependency_lock_sha256,
        'config', r.config
    ) INTO result FROM import_runs r
        JOIN datasets d ON d.id = r.dataset_id JOIN dataset_versions v ON v.id = r.dataset_version_id
        WHERE r.id = run_id;
    IF result IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '23503', MESSAGE = 'import run does not exist';
    END IF;
    RETURN result;
END;
$$;

CREATE FUNCTION recsys_snapshot_manifest(snapshot uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE AS $$
DECLARE result jsonb;
BEGIN
    SELECT jsonb_build_object(
        'input', recsys_input_document(s.created_by_import_run_id),
        'tracks', coalesce((SELECT jsonb_agg(jsonb_build_object('source_id', t.source_id,
            'metadata_sha256', m.content_sha256) ORDER BY t.source_id COLLATE "C")
            FROM snapshot_tracks st JOIN tracks t ON t.id = st.track_id
            LEFT JOIN track_metadata m ON m.id = st.track_metadata_id
            WHERE st.snapshot_id = s.id), '[]'::jsonb),
        'users', coalesce((SELECT jsonb_agg(u.source_id ORDER BY u.source_id COLLATE "C")
            FROM snapshot_users su JOIN users u ON u.id = su.user_id
            WHERE su.snapshot_id = s.id), '[]'::jsonb),
        'artifacts', coalesce((SELECT jsonb_object_agg(sa.role,
            jsonb_build_object('sha256', a.sha256, 'row_count', a.row_count))
            FROM snapshot_artifacts sa JOIN data_artifacts a ON a.id = sa.artifact_id
            WHERE sa.snapshot_id = s.id), '{}'::jsonb)
    ) INTO result FROM metadata_snapshots s WHERE s.id = snapshot;
    IF result IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '23503', MESSAGE = 'metadata snapshot does not exist';
    END IF;
    RETURN result;
END;
$$;

CREATE FUNCTION recsys_guard_run() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE document jsonb;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'import run cannot be deleted';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'running' OR NEW.input_sealed_at IS NOT NULL THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'new run must start unsealed and running';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status <> 'running' OR
       (to_jsonb(NEW) - ARRAY['input_sealed_at', 'status', 'finished_at', 'result_snapshot_id', 'stats', 'error_type'])
       IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['input_sealed_at', 'status', 'finished_at', 'result_snapshot_id', 'stats', 'error_type']) OR
       (OLD.input_sealed_at IS NOT NULL AND NEW.input_sealed_at IS DISTINCT FROM OLD.input_sealed_at) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'import inputs or finished run are immutable';
    END IF;
    IF OLD.input_sealed_at IS NULL AND NEW.status <> 'running' THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'seal import inputs before finishing';
    END IF;
    IF OLD.input_sealed_at IS NULL AND NEW.input_sealed_at IS NOT NULL THEN
        document := recsys_input_document(OLD.id);
        IF jsonb_array_length(document->'files') = 0 OR
           NEW.input_fingerprint IS DISTINCT FROM recsys_json_sha256(document) THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'input fingerprint or sources mismatch';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION recsys_guard_run_sources() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE parent_id uuid; sealed timestamptz;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'source reference updates are forbidden';
    END IF;
    IF TG_OP = 'DELETE' THEN parent_id := OLD.import_run_id; ELSE parent_id := NEW.import_run_id; END IF;
    SELECT input_sealed_at INTO sealed FROM import_runs WHERE id = parent_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '23503', MESSAGE = 'import run does not exist';
    END IF;
    IF sealed IS NOT NULL THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'sealed source references are immutable';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END;
$$;

CREATE FUNCTION recsys_guard_snapshot() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE creator record;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'snapshot cannot be deleted';
    END IF;
    IF TG_OP = 'INSERT' THEN
        SELECT * INTO creator FROM import_runs WHERE id = NEW.created_by_import_run_id;
        IF NOT FOUND OR creator.input_sealed_at IS NULL OR creator.status <> 'running' OR
           NEW.input_fingerprint IS DISTINCT FROM creator.input_fingerprint OR
           NEW.published_at IS NOT NULL THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'snapshot requires sealed running creator';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.published_at IS NOT NULL OR NEW.published_at IS NULL OR
       (to_jsonb(NEW) - 'published_at') IS DISTINCT FROM (to_jsonb(OLD) - 'published_at') THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'snapshot is immutable except one publication';
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION recsys_guard_snapshot_members() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE parent_id uuid; published timestamptz;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'snapshot member updates are forbidden';
    END IF;
    IF TG_OP = 'DELETE' THEN parent_id := OLD.snapshot_id; ELSE parent_id := NEW.snapshot_id; END IF;
    SELECT published_at INTO published FROM metadata_snapshots WHERE id = parent_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '23503', MESSAGE = 'snapshot does not exist';
    END IF;
    IF published IS NOT NULL THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'published snapshot members are immutable';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END;
$$;

-- INSERT時のNEWを検証せず、遅延実行時に最終状態を取り直す。
CREATE FUNCTION recsys_validate_run() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE current_run record; result record; document jsonb;
BEGIN
    SELECT * INTO current_run FROM import_runs WHERE id = NEW.id;
    IF NOT FOUND OR current_run.input_sealed_at IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'unsealed run cannot commit';
    END IF;
    document := recsys_input_document(current_run.id);
    IF jsonb_array_length(document->'files') = 0 OR
       current_run.input_fingerprint IS DISTINCT FROM recsys_json_sha256(document) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'input fingerprint or sources mismatch';
    END IF;
    IF current_run.status = 'succeeded' THEN
        SELECT * INTO result FROM metadata_snapshots WHERE id = current_run.result_snapshot_id;
        IF NOT FOUND OR result.published_at IS NULL OR
           result.input_fingerprint IS DISTINCT FROM current_run.input_fingerprint OR
           result.dataset_id <> current_run.dataset_id OR
           result.dataset_version_id <> current_run.dataset_version_id THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'successful run must reference matching published snapshot';
        END IF;
    END IF;
    RETURN NULL;
END;
$$;

CREATE FUNCTION recsys_validate_snapshot() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE snapshot record; creator record; expected jsonb; valid_outputs boolean;
BEGIN
    SELECT * INTO snapshot FROM metadata_snapshots WHERE id = NEW.id;
    IF NOT FOUND OR snapshot.published_at IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'unpublished snapshot cannot commit';
    END IF;
    SELECT * INTO creator FROM import_runs WHERE id = snapshot.created_by_import_run_id;
    IF NOT FOUND OR creator.status <> 'succeeded' OR
       creator.result_snapshot_id IS DISTINCT FROM snapshot.id OR creator.input_sealed_at IS NULL OR
       creator.input_fingerprint IS DISTINCT FROM snapshot.input_fingerprint THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'snapshot creator and result mismatch';
    END IF;
    expected := recsys_snapshot_manifest(snapshot.id);
    SELECT count(*) = 2 AND bool_and(a.format = 'parquet' AND a.row_count =
        CASE sa.role WHEN 'tracks' THEN (SELECT count(*) FROM snapshot_tracks WHERE snapshot_id = snapshot.id)
                     WHEN 'users' THEN (SELECT count(*) FROM snapshot_users WHERE snapshot_id = snapshot.id) END)
        INTO valid_outputs FROM snapshot_artifacts sa JOIN data_artifacts a ON a.id = sa.artifact_id
        WHERE sa.snapshot_id = snapshot.id;
    IF NOT coalesce(valid_outputs, false) OR snapshot.manifest IS DISTINCT FROM expected OR
       snapshot.content_fingerprint IS DISTINCT FROM recsys_json_sha256(expected - 'input') THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'snapshot manifest, output counts or content fingerprint mismatch';
    END IF;
    RETURN NULL;
END;
$$;

CREATE TRIGGER guard_run BEFORE INSERT OR UPDATE OR DELETE ON import_runs
    FOR EACH ROW EXECUTE FUNCTION recsys_guard_run();
CREATE TRIGGER guard_run_sources BEFORE INSERT OR UPDATE OR DELETE ON import_run_sources
    FOR EACH ROW EXECUTE FUNCTION recsys_guard_run_sources();
CREATE TRIGGER guard_snapshot BEFORE INSERT OR UPDATE OR DELETE ON metadata_snapshots
    FOR EACH ROW EXECUTE FUNCTION recsys_guard_snapshot();
CREATE TRIGGER guard_snapshot_tracks BEFORE INSERT OR UPDATE OR DELETE ON snapshot_tracks
    FOR EACH ROW EXECUTE FUNCTION recsys_guard_snapshot_members();
CREATE TRIGGER guard_snapshot_users BEFORE INSERT OR UPDATE OR DELETE ON snapshot_users
    FOR EACH ROW EXECUTE FUNCTION recsys_guard_snapshot_members();
CREATE TRIGGER guard_snapshot_artifacts BEFORE INSERT OR UPDATE OR DELETE ON snapshot_artifacts
    FOR EACH ROW EXECUTE FUNCTION recsys_guard_snapshot_members();

CREATE CONSTRAINT TRIGGER validate_run AFTER INSERT OR UPDATE ON import_runs
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION recsys_validate_run();
CREATE CONSTRAINT TRIGGER validate_snapshot AFTER INSERT ON metadata_snapshots
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION recsys_validate_snapshot();

-- TRUNCATEは行トリガーを通らないため、全表で文単位でも禁止する。
CREATE TRIGGER no_truncate BEFORE TRUNCATE ON datasets
    FOR EACH STATEMENT EXECUTE FUNCTION recsys_reject_mutation();
CREATE TRIGGER no_truncate BEFORE TRUNCATE ON dataset_versions
    FOR EACH STATEMENT EXECUTE FUNCTION recsys_reject_mutation();
CREATE TRIGGER no_truncate BEFORE TRUNCATE ON data_artifacts
    FOR EACH STATEMENT EXECUTE FUNCTION recsys_reject_mutation();
CREATE TRIGGER no_truncate BEFORE TRUNCATE ON source_files
    FOR EACH STATEMENT EXECUTE FUNCTION recsys_reject_mutation();
CREATE TRIGGER no_truncate BEFORE TRUNCATE ON tracks
    FOR EACH STATEMENT EXECUTE FUNCTION recsys_reject_mutation();
CREATE TRIGGER no_truncate BEFORE TRUNCATE ON users
    FOR EACH STATEMENT EXECUTE FUNCTION recsys_reject_mutation();
CREATE TRIGGER no_truncate BEFORE TRUNCATE ON track_metadata
    FOR EACH STATEMENT EXECUTE FUNCTION recsys_reject_mutation();
CREATE TRIGGER no_truncate BEFORE TRUNCATE ON import_runs
    FOR EACH STATEMENT EXECUTE FUNCTION recsys_reject_mutation();
CREATE TRIGGER no_truncate BEFORE TRUNCATE ON import_run_sources
    FOR EACH STATEMENT EXECUTE FUNCTION recsys_reject_mutation();
CREATE TRIGGER no_truncate BEFORE TRUNCATE ON metadata_snapshots
    FOR EACH STATEMENT EXECUTE FUNCTION recsys_reject_mutation();
CREATE TRIGGER no_truncate BEFORE TRUNCATE ON snapshot_tracks
    FOR EACH STATEMENT EXECUTE FUNCTION recsys_reject_mutation();
CREATE TRIGGER no_truncate BEFORE TRUNCATE ON snapshot_users
    FOR EACH STATEMENT EXECUTE FUNCTION recsys_reject_mutation();
CREATE TRIGGER no_truncate BEFORE TRUNCATE ON snapshot_artifacts
    FOR EACH STATEMENT EXECUTE FUNCTION recsys_reject_mutation();
