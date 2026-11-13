"""Domain errors."""


class HarnessDomainError(Exception):
    """Base error for domain rule violations."""


class InvalidRunTransitionError(HarnessDomainError):
    """Raised when a Run state transition is not part of the state machine."""


class NotFoundError(HarnessDomainError):
    """Raised when a tenant-scoped domain entity cannot be found."""


class ConflictError(HarnessDomainError):
    """Raised when an idempotency or optimistic concurrency rule is violated."""


class InvalidRequestError(HarnessDomainError):
    """Raised when a well-formed request references something it may not use."""


class SandboxGovernanceError(HarnessDomainError):
    """A Run was refused because the sandbox deployment cannot honour policy."""


class PermissionDeniedError(HarnessDomainError):
    """Raised when an authenticated actor cannot access a domain resource."""


class StorageCapacityError(HarnessDomainError):
    """Raised when durable artifact storage cannot accept more content."""


class EventSequenceConflictError(ConflictError):
    """Raised when another writer claims an event sequence first."""
