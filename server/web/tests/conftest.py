import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
_rs = str(_root)
if _rs in sys.path:
    sys.path.remove(_rs)
sys.path.insert(0, _rs)
