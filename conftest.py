"""Root conftest — adds project root to sys.path for tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
