#!/bin/bash
# Per-step setup: must delete /tests folder so that session-inheriting steps (inherit_prior_session=true) cannot read hidden tests from earlier steps
rm -rf /tests 2>/dev/null || true
# No other per-step fixtures needed for greenfield empty /app task
