import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
# Load bot.monitoring WITHOUT executing bot/__init__.py (which starts WZML services).
if "bot" not in sys.modules:
    stub = types.ModuleType("bot")
    stub.__path__ = [str(ROOT / "bot")]
    sys.modules["bot"] = stub
