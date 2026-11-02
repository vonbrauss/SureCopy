#!/bin/bash
# Double-click in Finder to open SureCopy (uses the project's .venv).
cd "$(dirname "$0")" && exec .venv/bin/python main.py
