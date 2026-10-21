"""Central configuration for the Ares matchmaking backend.

Everything environment-specific lives here. Override any value with the
matching environment variable.
"""

import os

# Database connection string. PostgreSQL form:
#   postgresql://user:password@host:5432/database
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://ares_user:ares_password@localhost:5432/ares_db"
)

# Regions the matchmaking loop sweeps, in priority order.
ACTIVE_REGIONS = ["IN", "US-EAST"]

# Match shape.
TEAM_SIZE = 5                 # players per team
PLAYERS_PER_MATCH = 2 * TEAM_SIZE

# Engine behaviour.
MATCHMAKER_TICK_SECONDS = 3   # how often the loop sweeps the queue
CLEANUP_TICK_SECONDS = 15     # how often stale players are purged
HEARTBEAT_TIMEOUT_SECONDS = 300  # queue entry dies without a heartbeat

# Queue fetch batch size per sweep.
QUEUE_BATCH_SIZE = 100

# A player waiting longer than this pulls the whole region into a GLOBAL
# (cross-region) match attempt instead of a LOCAL one.
GLOBAL_MATCH_WAIT_SECONDS = 60

# Elo matching tolerance: starts at BASE and relaxes by STEP every
# RELAX_EVERY_SECONDS the oldest player has been waiting.
ELO_THRESHOLD_BASE = 50
ELO_THRESHOLD_STEP = 50
ELO_THRESHOLD_RELAX_EVERY_SECONDS = 15

# Elo stakes applied when a match is resolved.
ELO_WIN_DELTA = 25
ELO_LOSS_DELTA = -20

# Fallback map when the maps table is empty.
DEFAULT_MAP = "Training Grounds"
