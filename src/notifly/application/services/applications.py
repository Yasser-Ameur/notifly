"""Application and API key use cases."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from notifly.application.dto import (
    ApplicationCreated,
    AuthenticatedContext,
    IssuedApiKey,
)
from notifly.application.security import generate_key, hash_key, key_prefix, verify_key
from notifly.application.services.audit import write_audit
from notifly.domain.enums import AuditAction
from notifly.domain.errors import (
    AlreadyExistsError,
    AuthenticationError,
    NotFoundError,
)
from notifly.domain.models.application import ApiKey, Application
from notifly.domain.ports.clock import Clock, SystemClock
from notifly.domain.ports.repositories import UnitOfWork, UnitOfWorkFactory

_BOOTSTRAP_ACTOR = "bootstrap"
_DEFAULT_KEY_NAME = "default"


class ApplicationService:
    """Use cases for application lifecycle, API keys, and authentication."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Clock | None = None,
        key_prefix_str: str = "notifly_",
        key_hash_iterations: int = 120_000,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()
        self._key_prefix = key_prefix_str
        self._key_iterations = key_hash_iterations

    # --- applications ---

    async def create_application(self, name: str) -> ApplicationCreated:
        """Create an application and its bootstrap API key (atomic)."""
        now = self._clock.now()
        async with self._uow_factory() as uow:
            existing = await uow.applications.get_by_name(name)
            if existing is not None:
                raise AlreadyExistsError(f"An application named {name!r} already exists.")

            application = Application(id=uuid4(), name=name, created_at=now, updated_at=now)
            await uow.applications.add(application)

            issued = self._build_api_key(application.id, _DEFAULT_KEY_NAME, now)
            await uow.api_keys.add(issued.api_key)

            await write_audit(
                uow,
                application_id=application.id,
                actor=_BOOTSTRAP_ACTOR,
                action=AuditAction.APPLICATION_CREATED,
                resource_type="application",
                resource_id=application.id,
                correlation_id=self._correlation_id(),
                now=now,
                payload={"name": name},
            )
            await self._audit_key_issued(uow, application.id, issued, _BOOTSTRAP_ACTOR, now)
        return ApplicationCreated(application=application, issued_key=issued)

    async def get_application(self, application_id: UUID) -> Application:
        async with self._uow_factory() as uow:
            return await self._require_application(uow, application_id)

    async def list_applications(self, *, limit: int, offset: int) -> list[Application]:
        async with self._uow_factory() as uow:
            return await uow.applications.list_apps(limit=limit, offset=offset)

    # --- api keys ---

    async def issue_api_key(self, application_id: UUID, name: str | None) -> IssuedApiKey:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            await self._require_application(uow, application_id)
            issued = self._build_api_key(application_id, name or _DEFAULT_KEY_NAME, now)
            await uow.api_keys.add(issued.api_key)
            await self._audit_key_issued(uow, application_id, issued, self._correlation_id(), now)
        return issued

    async def list_api_keys(self, application_id: UUID) -> list[ApiKey]:
        async with self._uow_factory() as uow:
            await self._require_application(uow, application_id)
            return await uow.api_keys.list_by_app(application_id)

    async def revoke_api_key(self, application_id: UUID, key_id: UUID) -> None:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            await self._require_application(uow, application_id)
            api_key = await uow.api_keys.get_by_id(key_id)
            if api_key is None or api_key.application_id != application_id:
                raise NotFoundError("API key not found.")
            if api_key.revoked_at is None:
                api_key.revoked_at = now
                await uow.api_keys.update(api_key)
                await write_audit(
                    uow,
                    application_id=application_id,
                    actor=self._correlation_id(),
                    action=AuditAction.API_KEY_REVOKED,
                    resource_type="api_key",
                    resource_id=key_id,
                    correlation_id=self._correlation_id(),
                    now=now,
                    payload={"key_prefix": api_key.key_prefix},
                )

    # --- authentication ---

    async def authenticate(self, raw_key: str | None) -> AuthenticatedContext:
        """Verify an API key and return its application context.

        Candidate keys are narrowed by the stored prefix first, then verified
        with a PBKDF2 derivation. The authenticated key's ``last_used_at`` is
        refreshed in the same transaction.
        """
        if raw_key is None or not raw_key.strip():
            raise AuthenticationError("A valid API key is required.")
        prefix = key_prefix(raw_key)

        async with self._uow_factory() as uow:
            candidates = await uow.api_keys.get_by_prefix(prefix)
            for api_key in candidates:
                if api_key.revoked_at is not None:
                    continue
                if not verify_key(raw_key, api_key.key_hash):
                    continue
                application = await uow.applications.get(api_key.application_id)
                if application is None:
                    raise AuthenticationError("API key references a missing application.")
                api_key.last_used_at = self._clock.now()
                await uow.api_keys.update(api_key)
                return AuthenticatedContext(application=application, api_key=api_key)
            raise AuthenticationError("Invalid or revoked API key.")

    # --- helpers ---

    def _build_api_key(self, application_id: UUID, name: str, now: datetime) -> IssuedApiKey:
        plaintext = generate_key(self._key_prefix)
        return IssuedApiKey(
            api_key=ApiKey(
                id=uuid4(),
                application_id=application_id,
                name=name,
                key_hash=hash_key(plaintext, self._key_iterations),
                key_prefix=key_prefix(plaintext),
                created_at=now,
            ),
            plaintext=plaintext,
        )

    async def _audit_key_issued(
        self,
        uow: UnitOfWork,
        application_id: UUID,
        issued: IssuedApiKey,
        actor: str,
        now: datetime,
    ) -> None:
        await write_audit(
            uow,
            application_id=application_id,
            actor=actor,
            action=AuditAction.API_KEY_ISSUED,
            resource_type="api_key",
            resource_id=issued.api_key.id,
            correlation_id=self._correlation_id(),
            now=now,
            payload={
                "name": issued.api_key.name,
                "key_prefix": issued.api_key.key_prefix,
            },
        )

    async def _require_application(self, uow: UnitOfWork, application_id: UUID) -> Application:
        application = await uow.applications.get(application_id)
        if application is None:
            raise NotFoundError(f"Application {application_id} does not exist.")
        return application

    def _correlation_id(self) -> str:
        from notifly.logging import get_correlation_id

        return get_correlation_id() or ""
