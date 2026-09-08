"""Password, OAuth, JWT and refresh-token authentication service."""

import base64
import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from uuid import uuid4

import httpx
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from pydantic import SecretStr

from harness.auth.models import (
    AccessClaims,
    AuthSession,
    AuthUser,
    Membership,
    RefreshToken,
    Role,
    TenantMember,
)
from harness.auth.repositories import AuthRepository
from harness.core.errors import ConflictError

OAuthProvider = Literal["google", "github"]
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class AuthenticationError(ValueError):
    """Raised when credentials or session tokens cannot be trusted."""

    def __init__(self, message: str, *, code: str = "access_token_invalid") -> None:
        super().__init__(message)
        self.code = code


class RegistrationDisabledError(ValueError):
    """Raised when public email registration is disabled."""


class MembershipPermissionError(ValueError):
    """Raised when a tenant member role transition is not permitted."""


@dataclass(frozen=True)
class OAuthProviderConfig:
    client_id: str
    client_secret: SecretStr

    @property
    def enabled(self) -> bool:
        return bool(self.client_id and self.client_secret.get_secret_value())


@dataclass(frozen=True)
class OAuthProfile:
    provider: OAuthProvider
    subject: str
    email: str
    display_name: str
    email_verified: bool


class AuthService:
    def __init__(
        self,
        repository: AuthRepository,
        *,
        jwt_secret: SecretStr,
        issuer: str,
        audience: str,
        access_token_minutes: int,
        refresh_token_days: int,
        allow_registration: bool,
        default_tenant_id: str,
        google: OAuthProviderConfig,
        github: OAuthProviderConfig,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._repository = repository
        self._jwt_secret = jwt_secret
        self._issuer = issuer
        self._audience = audience
        self._access_token_seconds = access_token_minutes * 60
        self._refresh_token_days = refresh_token_days
        self._allow_registration = allow_registration
        self._default_tenant_id = default_tenant_id
        self._providers = {"google": google, "github": github}
        self._passwords = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
        self._dummy_hash = self._passwords.hash("not-a-real-user-password")
        self._http_client = http_client

    @property
    def allow_registration(self) -> bool:
        return self._allow_registration

    @property
    def jwt_secret_length(self) -> int:
        return len(self._jwt_secret.get_secret_value())

    def provider_status(self) -> dict[str, bool]:
        return {name: config.enabled for name, config in self._providers.items()}

    async def register(self, *, email: str, password: str, display_name: str) -> AuthSession:
        if not self._allow_registration:
            raise RegistrationDisabledError("email registration is disabled")
        normalized_email = _normalize_email(email)
        _validate_password(password)
        name = display_name.strip() or normalized_email.split("@", 1)[0]
        now = datetime.now(UTC)
        user = AuthUser(
            user_id=f"user_{uuid4().hex}",
            email=normalized_email,
            display_name=name[:160],
            password_hash=self._passwords.hash(password),
            email_verified=False,
            disabled=False,
            created_at=now,
            updated_at=now,
        )
        role = (
            "owner"
            if await self._repository.count_members(self._default_tenant_id) == 0
            else "member"
        )
        membership = Membership(
            tenant_id=self._default_tenant_id,
            user_id=user.user_id,
            role=role,
            created_at=now,
        )
        await self._repository.create_user(user, membership)
        return await self._issue_session(user, membership)

    async def login(self, *, email: str, password: str) -> AuthSession:
        try:
            normalized_email = _normalize_email(email)
        except ValueError:
            normalized_email = email.strip().lower()
        user = await self._repository.get_user_by_email(normalized_email)
        encoded_hash = (
            self._dummy_hash if user is None or user.password_hash is None else user.password_hash
        )
        try:
            valid = self._passwords.verify(encoded_hash, password)
        except (InvalidHashError, VerifyMismatchError):
            valid = False
        if user is None or user.password_hash is None or not valid or user.disabled:
            raise AuthenticationError("email or password is incorrect")
        membership = await self._repository.get_membership(self._default_tenant_id, user.user_id)
        return await self._issue_session(user, membership)

    async def exchange_oauth_code(
        self,
        *,
        provider: OAuthProvider,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> AuthSession:
        config = self._providers[provider]
        if not config.enabled:
            raise AuthenticationError(f"{provider} sign-in is not configured")
        profile = await self._fetch_oauth_profile(
            provider=provider,
            config=config,
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=code_verifier,
        )
        if not profile.email_verified:
            raise AuthenticationError("the OAuth provider did not verify this email")
        user = await self._repository.get_oauth_user(provider, profile.subject)
        if user is None:
            user = await self._repository.get_user_by_email(profile.email)
            if user is None:
                now = datetime.now(UTC)
                user = AuthUser(
                    user_id=f"user_{uuid4().hex}",
                    email=profile.email,
                    display_name=profile.display_name[:160],
                    password_hash=None,
                    email_verified=True,
                    disabled=False,
                    created_at=now,
                    updated_at=now,
                )
                role = (
                    "owner"
                    if await self._repository.count_members(self._default_tenant_id) == 0
                    else "member"
                )
                membership = Membership(
                    tenant_id=self._default_tenant_id,
                    user_id=user.user_id,
                    role=role,
                    created_at=now,
                )
                try:
                    await self._repository.create_user(user, membership)
                except ConflictError:
                    user = await self._repository.get_user_by_email(profile.email)
                    if user is None:
                        raise
            await self._repository.link_oauth_identity(
                identity_id=f"oauth_{uuid4().hex}",
                provider=provider,
                subject=profile.subject,
                user_id=user.user_id,
                provider_email=profile.email,
                created_at=datetime.now(UTC),
            )
        if user.disabled:
            raise AuthenticationError("this account is disabled")
        membership = await self._repository.get_membership(self._default_tenant_id, user.user_id)
        return await self._issue_session(user, membership)

    async def refresh(self, raw_token: str) -> AuthSession:
        token_hash = _hash_token(raw_token)
        current = await self._repository.get_refresh_token(token_hash)
        now = datetime.now(UTC)
        if current is None:
            raise AuthenticationError("refresh token is invalid")
        if current.revoked_at is not None:
            await self._repository.revoke_token_family(current.family_id, now)
            raise AuthenticationError("refresh token reuse detected")
        if current.expires_at <= now:
            await self._repository.revoke_token_family(current.family_id, now)
            raise AuthenticationError("refresh token has expired")
        user = await self._repository.get_user(current.user_id)
        membership = await self._repository.get_membership(current.tenant_id, current.user_id)
        replacement_raw, replacement = self._new_refresh_token(
            user, membership, family_id=current.family_id
        )
        rotated = await self._repository.rotate_refresh_token(token_hash, replacement, now)
        if not rotated:
            await self._repository.revoke_token_family(current.family_id, now)
            raise AuthenticationError("refresh token reuse detected")
        return self._session_response(
            user=user,
            membership=membership,
            refresh_token=replacement_raw,
            family_id=replacement.family_id,
        )

    async def logout(self, raw_token: str) -> None:
        current = await self._repository.get_refresh_token(_hash_token(raw_token))
        if current is not None:
            await self._repository.revoke_token_family(current.family_id, datetime.now(UTC))

    def authenticate_access_token(self, token: str) -> AccessClaims:
        try:
            raw = jwt.decode(
                token,
                self._jwt_secret.get_secret_value(),
                algorithms=["HS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iat", "sub", "jti"]},
            )
            claims = AccessClaims.model_validate(raw)
        except (jwt.PyJWTError, ValueError) as error:
            raise AuthenticationError("access token is invalid or expired") from error
        return claims

    async def current_user(self, claims: AccessClaims) -> tuple[AuthUser, Membership]:
        if not await self._repository.is_token_family_active(
            claims.sub, claims.tenant_id, claims.sid, datetime.now(UTC)
        ):
            raise AuthenticationError(
                "This account was signed in on another device. Sign in again to continue.",
                code="session_replaced",
            )
        user = await self._repository.get_user(claims.sub)
        if user.disabled:
            raise AuthenticationError("this account is disabled")
        membership = await self._repository.get_membership(claims.tenant_id, claims.sub)
        if membership.role not in claims.roles:
            raise AuthenticationError("tenant membership has changed")
        return user, membership

    async def profile(self, tenant_id: str, user_id: str) -> tuple[AuthUser, Membership]:
        user = await self._repository.get_user(user_id)
        membership = await self._repository.get_membership(tenant_id, user_id)
        return user, membership

    async def list_members(self, tenant_id: str) -> list[TenantMember]:
        return [
            TenantMember(user=user, membership=membership)
            for user, membership in await self._repository.list_members(tenant_id)
        ]

    async def update_member_role(
        self,
        *,
        tenant_id: str,
        actor_user_id: str,
        target_user_id: str,
        role: Role,
    ) -> TenantMember:
        actor = await self._repository.get_membership(tenant_id, actor_user_id)
        target = await self._repository.get_membership(tenant_id, target_user_id)
        if actor.role not in {"owner", "admin"}:
            raise MembershipPermissionError("only owners and admins can manage members")
        if actor.role != "owner" and (
            target.role in {"owner", "admin"} or role in {"owner", "admin"}
        ):
            raise MembershipPermissionError("admins can only manage member and viewer roles")
        if (
            target.role == "owner"
            and role != "owner"
            and await self._repository.count_members_with_role(tenant_id, "owner") <= 1
        ):
            raise MembershipPermissionError("the workspace must keep at least one owner")
        updated = target.model_copy(update={"role": role})
        await self._repository.save_membership(updated)
        return TenantMember(
            user=await self._repository.get_user(target_user_id),
            membership=updated,
        )

    async def update_profile(self, *, tenant_id: str, user_id: str, display_name: str) -> AuthUser:
        await self._repository.get_membership(tenant_id, user_id)
        name = display_name.strip()
        if not name:
            raise ValueError("display name is required")
        user = await self._repository.get_user(user_id)
        return await self._repository.save_user(
            user.model_copy(update={"display_name": name[:160], "updated_at": datetime.now(UTC)})
        )

    async def change_password(
        self,
        *,
        tenant_id: str,
        user_id: str,
        current_password: str,
        new_password: str,
    ) -> None:
        await self._repository.get_membership(tenant_id, user_id)
        user = await self._repository.get_user(user_id)
        if user.password_hash is None:
            raise AuthenticationError("password login is not enabled for this account")
        try:
            valid = self._passwords.verify(user.password_hash, current_password)
        except (InvalidHashError, VerifyMismatchError):
            valid = False
        if not valid:
            raise AuthenticationError("current password is incorrect")
        _validate_password(new_password)
        if current_password == new_password:
            raise ValueError("new password must be different from the current password")
        now = datetime.now(UTC)
        await self._repository.save_user(
            user.model_copy(
                update={
                    "password_hash": self._passwords.hash(new_password),
                    "updated_at": now,
                }
            )
        )
        await self._repository.revoke_user_tokens(user_id, now)

    async def _issue_session(self, user: AuthUser, membership: Membership) -> AuthSession:
        raw_refresh, stored_refresh = self._new_refresh_token(user, membership)
        await self._repository.replace_user_refresh_token(stored_refresh, datetime.now(UTC))
        return self._session_response(
            user=user,
            membership=membership,
            refresh_token=raw_refresh,
            family_id=stored_refresh.family_id,
        )

    def _session_response(
        self,
        *,
        user: AuthUser,
        membership: Membership,
        refresh_token: str,
        family_id: str,
    ) -> AuthSession:
        now = datetime.now(UTC)
        payload = {
            "sub": user.user_id,
            "tenant_id": membership.tenant_id,
            "email": user.email,
            "name": user.display_name,
            "roles": [membership.role],
            "iss": self._issuer,
            "aud": self._audience,
            "token_type": "access",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=self._access_token_seconds)).timestamp()),
            "jti": f"access_{uuid4().hex}",
            "sid": family_id,
        }
        access_token = jwt.encode(payload, self._jwt_secret.get_secret_value(), algorithm="HS256")
        return AuthSession(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=self._access_token_seconds,
            user=user,
            membership=membership,
        )

    def _new_refresh_token(
        self,
        user: AuthUser,
        membership: Membership,
        *,
        family_id: str | None = None,
    ) -> tuple[str, RefreshToken]:
        raw = secrets.token_urlsafe(48)
        now = datetime.now(UTC)
        return raw, RefreshToken(
            token_hash=_hash_token(raw),
            family_id=family_id or f"session_{uuid4().hex}",
            user_id=user.user_id,
            tenant_id=membership.tenant_id,
            expires_at=now + timedelta(days=self._refresh_token_days),
            created_at=now,
        )

    async def _fetch_oauth_profile(
        self,
        *,
        provider: OAuthProvider,
        config: OAuthProviderConfig,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> OAuthProfile:
        if provider == "google":
            return await self._google_profile(config, code, redirect_uri, code_verifier)
        return await self._github_profile(config, code, redirect_uri, code_verifier)

    async def _google_profile(
        self,
        config: OAuthProviderConfig,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> OAuthProfile:
        async with self._client() as client:
            token_response = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": config.client_id,
                    "client_secret": config.client_secret.get_secret_value(),
                    "code": code,
                    "code_verifier": code_verifier,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                },
            )
            token_response.raise_for_status()
            access_token = cast(str, token_response.json()["access_token"])
            profile_response = await client.get(
                "https://openidconnect.googleapis.com/v1/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            profile_response.raise_for_status()
            profile = cast(dict[str, Any], profile_response.json())
        return OAuthProfile(
            provider="google",
            subject=str(profile["sub"]),
            email=_normalize_email(str(profile["email"])),
            display_name=str(profile.get("name") or profile["email"]),
            email_verified=bool(profile.get("email_verified")),
        )

    async def _github_profile(
        self,
        config: OAuthProviderConfig,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> OAuthProfile:
        async with self._client() as client:
            token_response = await client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": config.client_id,
                    "client_secret": config.client_secret.get_secret_value(),
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "code_verifier": code_verifier,
                },
            )
            token_response.raise_for_status()
            access_token = cast(str, token_response.json()["access_token"])
            headers = {
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {access_token}",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            profile_response = await client.get("https://api.github.com/user", headers=headers)
            emails_response = await client.get(
                "https://api.github.com/user/emails", headers=headers
            )
            profile_response.raise_for_status()
            emails_response.raise_for_status()
            profile = cast(dict[str, Any], profile_response.json())
            emails = cast(list[dict[str, Any]], emails_response.json())
        verified = [entry for entry in emails if entry.get("verified") and entry.get("email")]
        if not verified:
            raise AuthenticationError("GitHub account has no verified email")
        selected = next((entry for entry in verified if entry.get("primary")), verified[0])
        email = _normalize_email(str(selected["email"]))
        return OAuthProfile(
            provider="github",
            subject=str(profile["id"]),
            email=email,
            display_name=str(profile.get("name") or profile.get("login") or email),
            email_verified=True,
        )

    def _client(self) -> httpx.AsyncClient:
        if self._http_client is not None:
            return _BorrowedAsyncClient(self._http_client)
        return httpx.AsyncClient(timeout=15, follow_redirects=False)


class _BorrowedAsyncClient(httpx.AsyncClient):
    """Context wrapper that does not close a test-provided shared client."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._borrowed = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self._borrowed

    async def __aexit__(self, *args: object) -> None:
        return None


def _normalize_email(value: str) -> str:
    email = value.strip().lower()
    if len(email) > 320 or not EMAIL_PATTERN.fullmatch(email):
        raise ValueError("enter a valid email address")
    return email


def _validate_password(value: str) -> None:
    if len(value) < 10 or len(value) > 256:
        raise ValueError("password must contain 10 to 256 characters")
    if value.lower() == value or value.upper() == value or not any(ch.isdigit() for ch in value):
        raise ValueError("password must include uppercase, lowercase and a number")


def _hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def pkce_challenge(verifier: str) -> str:
    """Exposed for contract tests and compatible OAuth clients."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
