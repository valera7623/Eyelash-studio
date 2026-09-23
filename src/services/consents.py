from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.database.models.consent import DOCUMENT_PDN_PLATFORM, Consent
from src.database.models.user import User


def consent_text() -> str:
    path = settings.consent_pdn_path
    if path.exists():
        body = path.read_text(encoding="utf-8").strip()
        if len(body) > 3500:
            body = body[:3490] + "\n…"
        return body
    return (
        "Согласие на обработку персональных данных (152-ФЗ).\n"
        "Полный текст: data/legal/consent_pdn.md"
    )


async def record_consent(
    session: AsyncSession,
    user: User,
    *,
    studio_id: int | None,
) -> Consent:
    row = Consent(
        user_id=user.id,
        studio_id=studio_id,
        telegram_id=user.telegram_id,
        document_code=DOCUMENT_PDN_PLATFORM,
        document_version=settings.CONSENT_PDN_VERSION,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row
