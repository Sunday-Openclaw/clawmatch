-- More robust setup for pgcrypto
-- Some Supabase projects put extensions in a separate schema.
-- This forces it into public to be safe.

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;

-- Verify it works (this should not error if you run it in SQL editor)
-- SELECT encode(digest('test', 'sha256'), 'hex');
