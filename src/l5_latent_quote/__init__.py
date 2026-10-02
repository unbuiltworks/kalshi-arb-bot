"""L5 latent-quote realtime shadow. READ-ONLY SIDECAR.

This package contains NO order path. It performs public HTTP reads only, holds
no credentials, and emits hypothetical quotes to its own output directory. It
does not read or write anything belonging to the PMUS recorder or any other
experiment. See analysis/l5/latent_quote/PREREGISTRATION.md.
"""
READ_ONLY = True
SHADOW_ONLY = True
