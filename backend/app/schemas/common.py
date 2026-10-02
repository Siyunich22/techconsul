import re
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints

BIN_RE = re.compile(r"^\d{12}$")


def _check_bin(v: str) -> str:
    v = v.strip()
    if not BIN_RE.match(v):
        raise ValueError("БИН должен состоять из 12 цифр")
    return v


def _check_optional_bin(v: str) -> str:
    v = v.strip()
    return _check_bin(v) if v else v


Bin = Annotated[str, AfterValidator(_check_bin)]
OptionalBin = Annotated[str, AfterValidator(_check_optional_bin)]
Password = Annotated[str, StringConstraints(min_length=8, max_length=128)]
NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    page_size: int
