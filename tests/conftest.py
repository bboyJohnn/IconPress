import os

# Lets the GUI tests run without a display (CI, SSH sessions)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
