"""india-power-tracker pipeline package.

Layers: raw -> staging -> marts, with a rejects table and a run_log.
The same code runs against local Postgres or Neon via --target.
"""
