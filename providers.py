import abc
from typing import Any


class StreamSource(abc.ABC):
    """Abstract base class for stream sources.

    Subclasses must provide implementations for ``get_stream_url`` and
    ``get_metadata``.
    """

    @abc.abstractmethod
    def get_stream_url(self) -> str:
        """Return the URL or file path for the stream.

        The method should resolve the source location and return a string that
        can be used by the player to access the audio data.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def get_metadata(self) -> dict[str, Any]:
        """Return metadata for the stream.

        The returned dictionary may contain keys such as ``title``, ``artist``,
        ``album``, ``genre``, ``year`` and any other information relevant to
        the source.
        """
        raise NotImplementedError()
