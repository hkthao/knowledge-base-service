import sys
from pathlib import Path

# Make `kb_indexer` importable when running pytest from repo root or tests/.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
