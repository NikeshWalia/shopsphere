"""Shipping address endpoints.

Every query is scoped to the authenticated user, so requesting another
customer's address id returns 404 rather than their data.
"""

from __future__ import annotations

from fastapi import APIRouter, Path, status

from app.api.responses import AUTH_ERRORS, errors
from app.core.deps import CurrentUser, DbSession
from app.core.errors import AddressNotFoundError, InvalidOrderStateError
from app.models.user import Address
from app.repositories import user as user_repo
from app.schemas.address import AddressCreateRequest, AddressResponse, AddressUpdateRequest
from app.schemas.common import MessageResponse

router = APIRouter(prefix="/addresses", tags=["Addresses"])


@router.get(
    "",
    response_model=list[AddressResponse],
    summary="List the current user's addresses",
    description="Ordered with the default address first, then newest - the order a checkout form needs.",
    responses=AUTH_ERRORS,
)
def list_addresses(user: CurrentUser, db: DbSession) -> list[AddressResponse]:
    return [AddressResponse.model_validate(a) for a in user_repo.list_addresses(db, user.id)]


@router.post(
    "",
    response_model=AddressResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add an address",
    description=(
        "The first address a user creates automatically becomes their default, so checkout "
        "always has one to preselect."
    ),
    responses=errors(401, 403, 422),
)
def create_address(
    payload: AddressCreateRequest, user: CurrentUser, db: DbSession
) -> AddressResponse:
    existing = user_repo.list_addresses(db, user.id)
    make_default = payload.is_default or not existing

    address = Address(user_id=user.id, **payload.model_dump(exclude={"is_default"}))
    address.is_default = make_default
    db.add(address)
    db.flush()

    if make_default:
        user_repo.clear_default_addresses(db, user.id, except_id=address.id)

    db.commit()
    db.refresh(address)
    return AddressResponse.model_validate(address)


@router.get(
    "/{address_id}",
    response_model=AddressResponse,
    summary="Get an address",
    responses=errors(401, 403, 404),
)
def get_address(user: CurrentUser, db: DbSession, address_id: int = Path(ge=1)) -> AddressResponse:
    address = user_repo.get_address(db, address_id, user_id=user.id)
    if address is None:
        raise AddressNotFoundError(details={"address_id": address_id})
    return AddressResponse.model_validate(address)


@router.patch(
    "/{address_id}",
    response_model=AddressResponse,
    summary="Update an address",
    responses=errors(401, 403, 404, 422),
)
def update_address(
    payload: AddressUpdateRequest,
    user: CurrentUser,
    db: DbSession,
    address_id: int = Path(ge=1),
) -> AddressResponse:
    address = user_repo.get_address(db, address_id, user_id=user.id)
    if address is None:
        raise AddressNotFoundError(details={"address_id": address_id})

    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(address, field, value)

    if changes.get("is_default"):
        user_repo.clear_default_addresses(db, user.id, except_id=address.id)

    db.commit()
    db.refresh(address)
    return AddressResponse.model_validate(address)


@router.delete(
    "/{address_id}",
    response_model=MessageResponse,
    summary="Delete an address",
    description=(
        "Past orders keep their own snapshot of the shipping address, so deleting one here "
        "never changes order history. The last remaining address cannot be deleted while it "
        "is the default, to avoid leaving the account unable to check out."
    ),
    responses=errors(401, 403, 404, 409),
)
def delete_address(
    user: CurrentUser, db: DbSession, address_id: int = Path(ge=1)
) -> MessageResponse:
    address = user_repo.get_address(db, address_id, user_id=user.id)
    if address is None:
        raise AddressNotFoundError(details={"address_id": address_id})

    remaining = [a for a in user_repo.list_addresses(db, user.id) if a.id != address_id]
    if address.is_default and not remaining:
        raise InvalidOrderStateError(
            "Your only address cannot be deleted. Add another address first.",
            details={"address_id": address_id},
        )

    was_default = address.is_default
    db.delete(address)
    db.flush()

    if was_default and remaining:
        # Promote the newest surviving address so the account always has a default.
        remaining[0].is_default = True

    db.commit()
    return MessageResponse(message="Address deleted.")
