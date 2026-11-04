"""Full official checksum verification plus real local model loading."""
import sys
from setup import main

if __name__ == "__main__":
    sys.argv[1:] = ["verify", *sys.argv[1:]]
    sys.exit(main())
