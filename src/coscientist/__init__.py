"""CoScientist V4 deterministic core.

Nothing in this package calls an LLM. Everything here runs on cron with no
subscription. Judgment work is emitted as tickets (see `tickets.py`) and
drained separately, if and when quota exists.
"""

# The single source of truth for the version. pyproject.toml and the freeze
# manifest both read from here, and a test asserts they agree -- a package
# that reported three different versions in three places is not a package a
# provenance system can use.
__version__ = "4.2.0"
