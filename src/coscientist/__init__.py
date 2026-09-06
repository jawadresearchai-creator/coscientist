"""CoScientist V4 deterministic core.

Nothing in this package calls an LLM. Everything here can run without a model
subscription. Judgment work is emitted as persistent Director actions/tickets
and may be drained by a reasoning/repair plane when available.
"""

# Single source of truth for the engine version. pyproject.toml and freeze
# manifests read this value; provenance requires one version, not parallel ones.
__version__ = "4.6.0"
