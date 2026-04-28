"""Whisper Toggle compatibility marker for ufal whisper_streaming.

The upstream ufal repository is pinned in requirements.txt and staged by
scripts/deploy.sh because it is source-only and does not ship setup.py or
pyproject.toml. Runtime code imports its whisper_online.py module from the
deployed vendor directory.
"""

PINNED_REPO = "https://github.com/ufal/whisper_streaming"
PINNED_COMMIT = "6da90b44b7e50d79695e68166d2a2c7609c75abb"
