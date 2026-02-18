# Custom exceptions for Emendo

class EmendoError(Exception):
    """Base exception for Emendo errors."""
    pass

class VideoLoadError(EmendoError):
    """Raised when video file cannot be loaded."""
    pass

class MetadataError(EmendoError):
    """Raised when video metadata cannot be read."""
    pass

class ExportError(EmendoError):
    """Raised when export fails."""
    pass

class CodecError(EmendoError):
    """Raised when codec is not available."""
    pass

