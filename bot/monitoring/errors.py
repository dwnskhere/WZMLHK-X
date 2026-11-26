"""Monitoring-only error types. These never leave the monitoring package."""


class MonitoringError(Exception):
    """Base class for monitoring failures."""


class MonitoringConfigError(MonitoringError):
    """Monitoring is enabled but its configuration is unusable."""


class SectionUnavailable(MonitoringError):
    """A snapshot section could not be collected."""
