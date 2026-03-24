import sys
import os

# Add backend/ to Python path so that imports like
# `from supabase_client import ...` work when running tests from project root
backend_dir = os.path.join(os.path.dirname(__file__), '..', 'backend')
if backend_dir not in sys.path:
    sys.path.insert(0, os.path.abspath(backend_dir))

# Also add project root for `from backend.xxx import ...` style imports
project_root = os.path.join(os.path.dirname(__file__), '..')
if project_root not in sys.path:
    sys.path.insert(0, os.path.abspath(project_root))

# Set default env vars for tests
os.environ.setdefault("CLAWMATCH_SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("CLAWMATCH_SUPABASE_ANON_KEY", "test_key")
