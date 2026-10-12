CREATE FUNCTION recsys_reject_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'immutable registry row';
END;
$$;

CREATE TRIGGER immutable_datasets BEFORE UPDATE OR DELETE ON datasets
    FOR EACH ROW EXECUTE FUNCTION recsys_reject_mutation();
CREATE TRIGGER immutable_versions BEFORE UPDATE OR DELETE ON dataset_versions
    FOR EACH ROW EXECUTE FUNCTION recsys_reject_mutation();
CREATE TRIGGER immutable_artifacts BEFORE UPDATE OR DELETE ON data_artifacts
    FOR EACH ROW EXECUTE FUNCTION recsys_reject_mutation();
CREATE TRIGGER immutable_sources BEFORE UPDATE OR DELETE ON source_files
    FOR EACH ROW EXECUTE FUNCTION recsys_reject_mutation();
CREATE TRIGGER immutable_tracks BEFORE UPDATE OR DELETE ON tracks
    FOR EACH ROW EXECUTE FUNCTION recsys_reject_mutation();
CREATE TRIGGER immutable_users BEFORE UPDATE OR DELETE ON users
    FOR EACH ROW EXECUTE FUNCTION recsys_reject_mutation();
CREATE TRIGGER immutable_metadata BEFORE UPDATE OR DELETE ON track_metadata
    FOR EACH ROW EXECUTE FUNCTION recsys_reject_mutation();

CREATE FUNCTION recsys_validate_source_file() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE descriptor jsonb; size bigint; checksum text;
BEGIN
    SELECT entry INTO descriptor
    FROM dataset_versions v, jsonb_array_elements(v.source_manifest->'files') entry
    WHERE v.id = NEW.dataset_version_id AND v.dataset_id = NEW.dataset_id
        AND entry->>'name' = NEW.logical_name;
    SELECT byte_size, sha256 INTO size, checksum FROM data_artifacts
        WHERE id = NEW.artifact_id AND dataset_id = NEW.dataset_id;
    IF descriptor IS NULL OR size IS NULL OR
       (descriptor->>'byte_size')::numeric IS DISTINCT FROM size OR
       descriptor->'checksum'->>'algorithm' IS DISTINCT FROM NEW.expected_checksum_algorithm OR
       descriptor->'checksum'->>'value' IS DISTINCT FROM NEW.expected_checksum OR
       (NEW.expected_checksum_algorithm = 'sha256' AND checksum <> NEW.expected_checksum) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'source file does not match version manifest';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER validate_source BEFORE INSERT ON source_files
    FOR EACH ROW EXECUTE FUNCTION recsys_validate_source_file();
