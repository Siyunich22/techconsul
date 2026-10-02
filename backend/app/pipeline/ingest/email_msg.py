"""Письма: eml (stdlib), msg (extract-msg). Тело — страница документа, вложения — дочерние документы."""

import email
from email import policy
from pathlib import Path

from app.core.storage import safe_filename
from app.pipeline.ingest.base import Child, IngestError, Page, Parsed, clean_text


def _html_to_text(html: str) -> str:
    import re

    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", html)
    return re.sub(r"<[^>]+>", " ", html)


def parse_eml(path: Path, out_dir: Path) -> Parsed:
    msg = email.message_from_bytes(path.read_bytes(), policy=policy.default)
    header = "\n".join(f"{h}: {msg[h]}" for h in ("From", "To", "Date", "Subject") if msg[h])
    body_part = msg.get_body(preferencelist=("plain", "html"))
    body = ""
    if body_part is not None:
        body = body_part.get_content()
        if body_part.get_content_type() == "text/html":
            body = _html_to_text(body)
    children = []
    for i, part in enumerate(msg.iter_attachments()):
        name = part.get_filename() or f"attachment_{i + 1}"
        data = part.get_payload(decode=True) or b""
        target = out_dir / f"{i:03d}_{safe_filename(name)}"
        target.write_bytes(data)
        children.append(Child(name=name, relative_path=name, path=target))
    page = Page(no=1, text=clean_text(f"{header}\n\n{body}"))
    return Parsed(kind="email", pages=[page], children=children, meta={"subject": str(msg["Subject"] or "")})


def parse_msg(path: Path, out_dir: Path) -> Parsed:
    import extract_msg

    try:
        msg = extract_msg.openMsg(str(path))
    except Exception as exc:
        raise IngestError(f"Не удалось открыть письмо Outlook: {exc}") from exc
    try:
        header = "\n".join(
            f"{k}: {v}"
            for k, v in (("From", msg.sender), ("To", msg.to), ("Date", msg.date), ("Subject", msg.subject))
            if v
        )
        body = msg.body or (_html_to_text(msg.htmlBody.decode(errors="replace")) if msg.htmlBody else "")
        children = []
        for i, att in enumerate(msg.attachments):
            data = getattr(att, "data", None)
            if not isinstance(data, bytes):
                continue  # вложенные письма-объекты пропускаем
            name = att.longFilename or att.shortFilename or f"attachment_{i + 1}"
            target = out_dir / f"{i:03d}_{safe_filename(name)}"
            target.write_bytes(data)
            children.append(Child(name=name, relative_path=name, path=target))
        subject = msg.subject or ""
    finally:
        msg.close()
    return Parsed(
        kind="email",
        pages=[Page(no=1, text=clean_text(f"{header}\n\n{body}"))],
        children=children,
        meta={"subject": subject},
    )
