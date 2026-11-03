"""Small deterministic demonstration data."""

from datetime import UTC, datetime, timedelta
from typing import Any

from projects.helpdesk.repositories.base import (
    Category,
    HelpdeskRepository,
    Priority,
    Ticket,
    TicketHistory,
    TicketStatus,
)


class MockRepository(HelpdeskRepository):
    """Five tickets with different statuses for demonstration."""

    def __init__(self) -> None:
        """Create demonstration tickets."""
        now = datetime.now(UTC)
        self.tickets = [
            Ticket(
                id="TKT-001",
                title="无法登录邮箱系统",
                description="密码重置后仍然无法登录公司邮箱",
                category=Category.account,
                priority=Priority.high,
                status=TicketStatus.open,
                requester="张三",
                created_at=now - timedelta(hours=2),
                updated_at=now - timedelta(hours=2),
            ),
            Ticket(
                id="TKT-002",
                title="办公室网络连接不稳定",
                description="3楼办公区域WiFi频繁掉线",
                category=Category.network,
                priority=Priority.medium,
                status=TicketStatus.assigned,
                requester="李四",
                assignee="网络组",
                created_at=now - timedelta(hours=5),
                updated_at=now - timedelta(hours=1),
            ),
            Ticket(
                id="TKT-003",
                title="打印机无法正常工作",
                description="会议室打印机显示错误代码E05",
                category=Category.hardware,
                priority=Priority.low,
                status=TicketStatus.in_progress,
                requester="王五",
                assignee="硬件组",
                created_at=now - timedelta(days=1),
                updated_at=now - timedelta(minutes=30),
            ),
            Ticket(
                id="TKT-004",
                title="CRM系统访问缓慢",
                description="客户管理系统响应时间超过10秒",
                category=Category.software,
                priority=Priority.high,
                status=TicketStatus.open,
                requester="赵六",
                created_at=now - timedelta(minutes=45),
                updated_at=now - timedelta(minutes=45),
            ),
            Ticket(
                id="TKT-005",
                title="申请新员工账号",
                description="新入职员工需要开通系统访问权限",
                category=Category.account,
                priority=Priority.medium,
                status=TicketStatus.resolved,
                requester="人事部",
                assignee="系统管理员",
                created_at=now - timedelta(days=2),
                updated_at=now - timedelta(hours=3),
                resolved_at=now - timedelta(hours=3),
            ),
        ]
        self.history: dict[str, list[TicketHistory]] = {
            "TKT-002": [
                TicketHistory(
                    ticket_id="TKT-002",
                    timestamp=now - timedelta(hours=5),
                    action="created",
                    operator="李四",
                    details={"priority": "medium"},
                ),
                TicketHistory(
                    ticket_id="TKT-002",
                    timestamp=now - timedelta(hours=1),
                    action="assigned",
                    operator="系统管理员",
                    details={"assignee": "网络组"},
                ),
            ]
        }

    async def query_tickets(
        self,
        status: TicketStatus | None = None,
        priority: Priority | None = None,
        category: Category | None = None,
        assignee: str | None = None,
    ) -> list[Ticket]:
        """Apply filters to demonstration tickets."""
        return [
            item.model_copy(deep=True)
            for item in self.tickets
            if (status is None or item.status == status)
            and (priority is None or item.priority == priority)
            and (category is None or item.category == category)
            and (assignee is None or item.assignee == assignee)
        ]

    async def get_ticket(self, ticket_id: str) -> Ticket | None:
        """Find ticket by ID."""
        for ticket in self.tickets:
            if ticket.id == ticket_id:
                return ticket.model_copy(deep=True)
        return None

    async def update_ticket(
        self,
        ticket_id: str,
        status: TicketStatus | None = None,
        assignee: str | None = None,
        priority: Priority | None = None,
    ) -> dict[str, Any]:
        """Update ticket fields in memory."""
        for ticket in self.tickets:
            if ticket.id == ticket_id:
                updates = {}
                if status is not None:
                    ticket.status = status
                    updates["status"] = status.value
                    if status == TicketStatus.resolved:
                        ticket.resolved_at = datetime.now(UTC)
                if assignee is not None:
                    ticket.assignee = assignee
                    updates["assignee"] = assignee
                if priority is not None:
                    ticket.priority = priority
                    updates["priority"] = priority.value
                ticket.updated_at = datetime.now(UTC)
                return {"ticket_id": ticket_id, "updated": updates}
        return {"ticket_id": ticket_id, "error": "not_found"}

    async def get_ticket_history(self, ticket_id: str, limit: int = 10) -> list[TicketHistory]:
        """Return stored history for demonstration."""
        return self.history.get(ticket_id, [])[:limit]

    async def check_duplicate(self, title: str, category: Category) -> list[Ticket]:
        """Simple keyword match on open tickets."""
        keywords = set(title.lower().split())
        return [
            ticket.model_copy(deep=True)
            for ticket in self.tickets
            if ticket.status in [TicketStatus.open, TicketStatus.assigned]
            and ticket.category == category
            and any(word in ticket.title.lower() for word in keywords)
        ]
