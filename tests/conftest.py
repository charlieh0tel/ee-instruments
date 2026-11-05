import os

# matplotlib picks a backend at import time; force the headless one before
# any test module pulls in pyplot.
os.environ.setdefault("MPLBACKEND", "Agg")
