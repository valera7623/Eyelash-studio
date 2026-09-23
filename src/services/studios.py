from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.studio import Resource, Studio
from src.database.models.user import User
from src.utils.slug import slugify


async def get_owner_studio(session: AsyncSession, user: User) -> Studio | None:
    stmt = select(Studio).where(Studio.owner_id == user.id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_studio_by_slug(session: AsyncSession, slug: str) -> Studio | None:
    stmt = select(Studio).where(Studio.slug == slug)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_primary_resource(session: AsyncSession, studio_id: int) -> Resource | None:
    stmt = (
        select(Resource)
        .where(Resource.studio_id == studio_id, Resource.is_active.is_(True))
        .order_by(Resource.id.asc())
    )
    return (await session.execute(stmt)).scalars().first()


async def list_active_resources(session: AsyncSession, studio_id: int) -> list[Resource]:
    stmt = (
        select(Resource)
        .where(Resource.studio_id == studio_id, Resource.is_active.is_(True))
        .order_by(Resource.id.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def unique_slug(session: AsyncSession, name: str) -> str:
    base = slugify(name)
    candidate = base
    suffix = 2
    while await get_studio_by_slug(session, candidate):
        candidate = f"{base}-{suffix}"[:64]
        suffix += 1
    return candidate
