"""Uses SHARED_PREFIX from pkg_a."""

from pkg_a.constants import SHARED_PREFIX


def get_prefix() -> str:
    return SHARED_PREFIX
