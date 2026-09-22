"""Agent Name Service integration.

Registration and certificate lifecycle happen out of band through `ans-cli`
(see `docs/ANS.md`). This package is the runtime half: it resolves an ANSName
through DNS, checks the Transparency Log badge, and verifies the ANS-6 Method B
proof of possession that an agent attaches to every privileged call.
"""

from server.app.ans.names import ANSName, InvalidANSName

__all__ = ["ANSName", "InvalidANSName"]
