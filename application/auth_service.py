"""
Application service — authentication use case.
"""

from domain.models import LoginCredentials
from domain.ports import IAuthPort


class AuthService:
    """
    Orchestrates the login use case and tracks authentication state.
    Does not know how login is performed — that is the responsibility of IAuthPort.
    """

    def __init__(self, auth_port: IAuthPort) -> None:
        self._auth_port = auth_port
        self.is_authenticated: bool = False

    async def login(self, credentials: LoginCredentials) -> str:
        """
        Execute the login flow via the injected port.
        Sets is_authenticated = True on success, raises on failure.
        """
        await self._auth_port.login(credentials)
        self.is_authenticated = True
        return "Login successful."

    def require_authenticated(self) -> None:
        """Raise RuntimeError if the user has not logged in yet."""
        if not self.is_authenticated:
            raise RuntimeError(
                "Not authenticated. Call the 'login' tool before using other tools."
            )
