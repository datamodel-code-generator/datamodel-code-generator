from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.security import HTTPAuthorizationCredentials

from app.models import Photo, PhotoUpload
from app.server import Credentials, RequirementSets, build_router, serve_source_openapi, validation_error_handler
from app.server.services import PhotosService

USERS = {"secret-token": "alice"}


def authorize(requirement_sets: RequirementSets, credentials: Credentials) -> str:
    bearer = credentials["bearer"]
    if isinstance(bearer, HTTPAuthorizationCredentials) and (user := USERS.get(bearer.credentials)):
        return user
    raise HTTPException(403, "Unknown token")


def tenant(request: Request, x_tenant: Annotated[str, Header()] = "public") -> None:
    request.state.tenant = x_tenant


class PhotoStore:
    def __init__(self) -> None:
        self.photos: dict[tuple[str, int], Photo] = {}


class Photos(PhotosService[str]):
    def __init__(self, store: PhotoStore) -> None:
        self.store = store

    async def upload_photo(self, *, request: Request, principal: str, pet_id: int, body: PhotoUpload) -> Photo:
        photo = Photo(pet_id=pet_id, size=len(body.file), owner=principal, caption=body.caption)
        self.store.photos[request.state.tenant, pet_id] = photo
        return photo

    def get_photo(self, *, request: Request, pet_id: int) -> Photo:
        if (photo := self.store.photos.get((request.state.tenant, pet_id))) is None:
            raise HTTPException(404, f"Pet {pet_id} has no photo")
        return photo


app = FastAPI()
app.include_router(build_router(photos=Photos(PhotoStore()), authorize=authorize, dependencies=[Depends(tenant)]))
app.add_exception_handler(RequestValidationError, validation_error_handler)
serve_source_openapi(app)
