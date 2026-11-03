"""Compatibility entry point for vehicle installations using the original worker."""
from pathlib import Path
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fos_rag.isolated_model_worker import complete, main

if __name__ == "__main__":
    main()
