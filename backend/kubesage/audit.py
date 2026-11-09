from kubesage.db.models import AuditLog
from kubesage.db.session import session_scope


def audit(event: str, target: str | None = None, actor: str = "kubesage", **details) -> None:
    """Append one audit entry in its own transaction, so it survives a failure of the surrounding work."""
    with session_scope() as session:
        session.add(AuditLog(actor=actor, event=event, target=target, details=details))
