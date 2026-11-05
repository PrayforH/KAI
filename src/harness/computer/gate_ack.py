"""The one acknowledgement contract between a node and its guest gate.

Both the Docker driver's start/connect/finish confirmations and the VM
recovery drain must prove the same thing: the guest executed the action for
exactly this lease and left it in exactly the requested state. Anything less
keeps the caller recovering instead of reusing a possibly live instance.
"""

import json

from harness.sandbox.base import SandboxCommandResult


class GateAckError(RuntimeError):
    """The guest's acknowledgement does not confirm the requested lease."""


def validate(
    result: SandboxCommandResult, *, epoch: int, lease_id: str, active: bool
) -> None:
    """Refuse any response that is not this lease's own confirmation.

    Non-zero exits, unparsable bodies, missing fields, a superseded epoch, a
    foreign lease id and an unexpected active state are all refusals. The
    guest state body also carries its kernel boot identity, which the guest
    itself has already checked; the node's contract is the lease triple.
    """

    if result.exit_code != 0:
        raise GateAckError("computer control acknowledgement reported a failed action") from None
    try:
        value = json.loads(result.stdout)
    except ValueError:
        raise GateAckError("computer control acknowledgement is invalid") from None
    state = value if isinstance(value, dict) else {}
    if (
        state.get("epoch") != epoch
        or state.get("lease_id") != lease_id
        or state.get("active") is not active
    ):
        raise GateAckError("computer control acknowledgement does not match its lease") from None
