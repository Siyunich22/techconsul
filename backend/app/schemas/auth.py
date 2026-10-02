import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr

from app.models import UserRole
from app.schemas.common import Bin, NonEmpty, ORMModel, Password


class OrgIn(BaseModel):
    name: NonEmpty
    bin: Bin
    address: str = ""


class OwnerIn(BaseModel):
    full_name: NonEmpty
    email: EmailStr
    password: Password
    position: str = ""
    phone: str = ""


class RegisterIn(BaseModel):
    organization: OrgIn
    user: OwnerIn


class LoginIn(BaseModel):
    # без проверки формата: пользователь ищется по email, формат проверен при регистрации
    email: str
    password: str


class ForgotIn(BaseModel):
    email: str


class ResetIn(BaseModel):
    token: str
    password: Password


class InvitationInfo(BaseModel):
    email: str
    full_name: str
    organization_name: str
    role: UserRole


class AcceptInviteIn(BaseModel):
    token: str
    password: Password
    full_name: str | None = None


class OrgBrief(ORMModel):
    id: uuid.UUID
    name: str
    bin: str


class MeOut(ORMModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: UserRole
    position: str
    phone: str
    organization: OrgBrief
    last_login_at: datetime | None


class MePatch(BaseModel):
    full_name: NonEmpty | None = None
    position: str | None = None
    phone: str | None = None


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: Password
