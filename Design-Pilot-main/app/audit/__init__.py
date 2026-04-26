"""
Audit logging.

Append-only log of every state-changing action. Used for:
- Security forensics (who did what, when)
- Compliance (GDPR, Saudi PDPL, SOC 2 Type II)
- Debugging production issues

Retention: 2 years (standard), 7 years (enterprise tier).
Database-level policy revokes DELETE/UPDATE on the audit_log table.
"""
from app.audit.service import AuditService, audit_service
from app.audit.middleware import AuditLogMiddleware

__all__ = ["AuditService", "audit_service", "AuditLogMiddleware"]
