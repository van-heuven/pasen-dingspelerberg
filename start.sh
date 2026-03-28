#!/bin/bash
cd "$(dirname "$0")"

# Installeer Flask als dat nog niet gebeurd is
if ! python3 -c "import flask" 2>/dev/null; then
    echo "Flask installeren..."
    pip3 install flask --quiet
fi

python3 app.py
