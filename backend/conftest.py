import os
import sys

# Ensure the ``app`` package (backend/app) is importable when pytest is invoked
# from anywhere. pytest inserts the conftest directory into sys.path, but this
# keeps discovery robust regardless of the working directory.
sys.path.insert(0, os.path.dirname(__file__))
