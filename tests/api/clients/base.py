"""Base class for the API clients.

Clients speak the domain's language ("add this product to the cart"), not
HTTP's ("POST /cart/items with this body"). Tests read as business intent, and
an endpoint that moves or is renamed is fixed in one place rather than in fifty
tests.

Clients never assert. They return an :class:`ApiResponse` and let the test
decide what "correct" means - which is what allows the same client method to be
used by a happy-path test and by a negative test expecting a 409.
"""

from __future__ import annotations

from tests.utilities.http import ApiResponse, HttpClient


class BaseClient:
    def __init__(self, http: HttpClient) -> None:
        self.http = http

    @property
    def token(self) -> str | None:
        return self.http.token

    def as_anonymous(self) -> ApiResponse:  # pragma: no cover - documentation helper
        raise NotImplementedError

    def _get(self, path: str, **kwargs: object) -> ApiResponse:
        return self.http.get(path, **kwargs)

    def _post(self, path: str, **kwargs: object) -> ApiResponse:
        return self.http.post(path, **kwargs)

    def _put(self, path: str, **kwargs: object) -> ApiResponse:
        return self.http.put(path, **kwargs)

    def _patch(self, path: str, **kwargs: object) -> ApiResponse:
        return self.http.patch(path, **kwargs)

    def _delete(self, path: str, **kwargs: object) -> ApiResponse:
        return self.http.delete(path, **kwargs)
