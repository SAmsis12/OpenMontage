"""OpenMontage's bounded, read-only Hermes stdio integration."""

from .contract import handle_request, process_request_bytes

__all__ = ["handle_request", "process_request_bytes"]
