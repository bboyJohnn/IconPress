import sys

if len(sys.argv) > 1 and sys.argv[1] in ("convert", "icon"):  # python -m iconpress convert …
    from .cli import main

    sys.exit(main(sys.argv[1:]))

from .app import main

sys.exit(main())
