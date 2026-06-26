#!/bin/bash

# Oracle solution for username swap race condition task.
# Copies the fixed username_service.py to replace the buggy version.

# Replace the buggy username_service.py with the fixed version
cp /solution/username_service_fixed.py /app/username_service.py

echo "Solution applied: username_service.py replaced with fixed version"
