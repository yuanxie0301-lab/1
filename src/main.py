import sys
from pathlib import Path

THIS = Path(__file__).resolve()
sys.path.insert(0, str(THIS.parent))

from ai_reception.app import run_app

if __name__ == "__main__":
    run_app()
