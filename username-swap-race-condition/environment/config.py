"""Configuration for username service."""

# Hold time in seconds for username holds (turned down for reasonable test execution)
HOLD_TIME_SECONDS = 3  # Increased for testing

# Cache update interval in seconds (how often read caches update from global state)
CACHE_UPDATE_INTERVAL = 0.5

# Dangling pointers can only be claimed if older than this lockout period
# (2x cache update interval to prevent races with in-flight updates)
DANGLING_POINTER_LOCKOUT = 2 * CACHE_UPDATE_INTERVAL
