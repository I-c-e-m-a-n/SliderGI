"""Download/select official model; uses the same verified setup path as the CLI."""
import sys
from setup import main

if __name__ == "__main__":
    sys.argv[1:] = ["model", *(sys.argv[1:] or ["7b"])]
    sys.exit(main())
