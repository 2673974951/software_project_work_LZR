"""让测试在未安装包的情况下也能直接跑：把 src 加入 sys.path."""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
