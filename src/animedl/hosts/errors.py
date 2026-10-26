class ResolveError(Exception):
    """A video host did not give us a downloadable stream."""


class FileGone(ResolveError):
    """The host says the file was deleted."""


class TryLater(ResolveError):
    """The host asks to wait (free download limits): not a failure of the episode."""
