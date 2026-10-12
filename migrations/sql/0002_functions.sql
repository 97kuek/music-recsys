-- 同じ内容を同じhashにする。JSONの数値は指数・末尾の0を正規化する。
CREATE FUNCTION recsys_canonical_json(value jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE result text;
BEGIN
    CASE jsonb_typeof(value)
        WHEN 'object' THEN
            SELECT '{' || coalesce(string_agg(to_jsonb(key)::text || ':' ||
                recsys_canonical_json(val), ',' ORDER BY key COLLATE "C"), '') || '}'
            INTO result FROM jsonb_each(value) AS entries(key, val);
        WHEN 'array' THEN
            SELECT '[' || coalesce(string_agg(recsys_canonical_json(val), ',' ORDER BY n), '') || ']'
            INTO result FROM jsonb_array_elements(value) WITH ORDINALITY AS entries(val, n);
        WHEN 'number' THEN result := trim_scale((value #>> '{}')::numeric)::text;
        ELSE result := value::text;
    END CASE;
    RETURN result;
END;
$$;

CREATE FUNCTION recsys_json_sha256(value jsonb) RETURNS text
LANGUAGE sql IMMUTABLE STRICT AS $$
    SELECT encode(sha256(convert_to(recsys_canonical_json(value), 'UTF8')), 'hex');
$$;

CREATE FUNCTION recsys_valid_tags(payload jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE entry record;
BEGIN
    IF jsonb_typeof(payload) <> 'object' OR
       jsonb_typeof(payload->'tags') IS DISTINCT FROM 'object' THEN RETURN false; END IF;
    FOR entry IN SELECT * FROM jsonb_each(payload->'tags') LOOP
        IF entry.key ~ '^[[:space:]]*$' OR jsonb_typeof(entry.value) <> 'number' THEN
            RETURN false;
        END IF;
        IF (entry.value::text)::numeric NOT BETWEEN 1 AND 100 OR
           (entry.value::text)::numeric <> trunc((entry.value::text)::numeric) THEN
            RETURN false;
        END IF;
    END LOOP;
    RETURN true;
END;
$$;

CREATE FUNCTION recsys_valid_source_manifest(manifest jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE entry jsonb; names text[] := '{}'; algorithm text; checksum text;
BEGIN
    IF jsonb_typeof(manifest) <> 'object' OR
       jsonb_typeof(manifest->'files') IS DISTINCT FROM 'array' THEN RETURN false; END IF;
    FOR entry IN SELECT * FROM jsonb_array_elements(manifest->'files') LOOP
        IF jsonb_typeof(entry) <> 'object' OR
           jsonb_typeof(entry->'name') IS DISTINCT FROM 'string' OR
           entry->>'name' ~ '^[[:space:]]*$' OR entry->>'name' = ANY(names) OR
           jsonb_typeof(entry->'byte_size') IS DISTINCT FROM 'number' THEN RETURN false; END IF;
        IF (entry->>'byte_size')::numeric < 0 OR
           (entry->>'byte_size')::numeric <> trunc((entry->>'byte_size')::numeric) THEN
            RETURN false;
        END IF;
        names := array_append(names, entry->>'name');
        IF entry ? 'checksum' AND entry->'checksum' <> 'null'::jsonb THEN
            IF jsonb_typeof(entry->'checksum') <> 'object' OR
               jsonb_typeof(entry->'checksum'->'algorithm') IS DISTINCT FROM 'string' OR
               jsonb_typeof(entry->'checksum'->'value') IS DISTINCT FROM 'string' THEN
                RETURN false;
            END IF;
            algorithm := entry->'checksum'->>'algorithm';
            checksum := entry->'checksum'->>'value';
            IF NOT ((algorithm = 'md5' AND checksum ~ '^[0-9a-f]{32}$') OR
                    (algorithm = 'sha256' AND checksum ~ '^[0-9a-f]{64}$')) THEN
                RETURN false;
            END IF;
        END IF;
    END LOOP;
    RETURN true;
END;
$$;
