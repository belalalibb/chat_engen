"""Conversation stores: a Protocol + in-memory implementation (default).

PostgreSQL (``ce_project / ce_conversation / ce_turn``) is the D10 target and
plugs in behind the same Protocol; absent ``DATABASE_URL`` the in-memory store
serves (honest single-process posture, see /healthz).
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from chat_engine.conversations.contracts import Conversation, Project, Turn


class NotFound(LookupError):
    def __init__(self, kind: str, ident: UUID | str) -> None:
        self.kind, self.ident = kind, ident
        super().__init__(f"{kind} not found: {ident}")


class ConversationStorePort(Protocol):
    backend_name: str

    # projects
    def put_project(self, project: Project) -> Project: ...
    def get_project(self, tenant_id: UUID, project_id: UUID) -> Project: ...
    def list_projects(self, tenant_id: UUID) -> list[Project]: ...
    def delete_project(self, tenant_id: UUID, project_id: UUID) -> None: ...
    # conversations
    def put_conversation(self, conversation: Conversation) -> Conversation: ...
    def get_conversation(self, tenant_id: UUID, conversation_id: UUID) -> Conversation: ...
    def list_conversations(self, tenant_id: UUID, *, project_id: UUID | None = None, include_archived: bool = False) -> list[Conversation]: ...
    def delete_conversation(self, tenant_id: UUID, conversation_id: UUID) -> None: ...
    # turns
    def append_turn(self, turn: Turn) -> Turn: ...
    def update_turn(self, turn: Turn) -> Turn: ...
    def turns(self, conversation_id: UUID, *, after_seq: int | None = None, limit: int | None = None) -> list[Turn]: ...
    def next_seq(self, conversation_id: UUID) -> int: ...


class InMemoryConversationStore:
    backend_name = "in_memory"

    def __init__(self) -> None:
        self._projects: dict[UUID, Project] = {}
        self._conversations: dict[UUID, Conversation] = {}
        self._turns: dict[UUID, list[Turn]] = {}

    # -- projects -----------------------------------------------------------------------

    def put_project(self, project: Project) -> Project:
        self._projects[project.id] = project
        return project

    def get_project(self, tenant_id: UUID, project_id: UUID) -> Project:
        p = self._projects.get(project_id)
        if p is None or p.tenant_id != tenant_id:
            raise NotFound("project", project_id)
        return p

    def list_projects(self, tenant_id: UUID) -> list[Project]:
        return sorted((p for p in self._projects.values() if p.tenant_id == tenant_id), key=lambda p: p.created_at)

    def delete_project(self, tenant_id: UUID, project_id: UUID) -> None:
        self.get_project(tenant_id, project_id)
        del self._projects[project_id]

    # -- conversations ------------------------------------------------------------------

    def put_conversation(self, conversation: Conversation) -> Conversation:
        self._conversations[conversation.id] = conversation
        self._turns.setdefault(conversation.id, [])
        return conversation

    def get_conversation(self, tenant_id: UUID, conversation_id: UUID) -> Conversation:
        c = self._conversations.get(conversation_id)
        if c is None or c.tenant_id != tenant_id:
            raise NotFound("conversation", conversation_id)
        return c

    def list_conversations(self, tenant_id: UUID, *, project_id: UUID | None = None, include_archived: bool = False) -> list[Conversation]:
        rows = [
            c
            for c in self._conversations.values()
            if c.tenant_id == tenant_id
            and (project_id is None or c.project_id == project_id)
            and (include_archived or not c.archived)
        ]
        return sorted(rows, key=lambda c: c.updated_at, reverse=True)

    def delete_conversation(self, tenant_id: UUID, conversation_id: UUID) -> None:
        self.get_conversation(tenant_id, conversation_id)
        del self._conversations[conversation_id]
        self._turns.pop(conversation_id, None)

    # -- turns --------------------------------------------------------------------------

    def append_turn(self, turn: Turn) -> Turn:
        self._turns.setdefault(turn.conversation_id, []).append(turn)
        return turn

    def update_turn(self, turn: Turn) -> Turn:
        rows = self._turns.get(turn.conversation_id, [])
        for i, t in enumerate(rows):
            if t.id == turn.id:
                rows[i] = turn
                return turn
        raise NotFound("turn", turn.id)

    def turns(self, conversation_id: UUID, *, after_seq: int | None = None, limit: int | None = None) -> list[Turn]:
        rows = self._turns.get(conversation_id, [])
        if after_seq is not None:
            rows = [t for t in rows if t.seq > after_seq]
        if limit is not None:
            rows = rows[-limit:]
        return list(rows)

    def next_seq(self, conversation_id: UUID) -> int:
        rows = self._turns.get(conversation_id, [])
        return rows[-1].seq + 1 if rows else 0
