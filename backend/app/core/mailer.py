"""Отправка писем. В dev письма уходят в Mailpit (http://localhost:8025)."""

import logging
import smtplib
from email.message import EmailMessage
from typing import Protocol

from app.core.config import get_settings

log = logging.getLogger(__name__)


class Mailer(Protocol):
    def send(self, to: str, subject: str, text: str) -> None: ...


class SmtpMailer:
    def send(self, to: str, subject: str, text: str) -> None:
        s = get_settings()
        msg = EmailMessage()
        msg["From"] = s.smtp_from
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(text)
        try:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10) as smtp:
                if s.smtp_user:
                    smtp.starttls()
                    smtp.login(s.smtp_user, s.smtp_password)
                smtp.send_message(msg)
        except OSError:
            # Письмо не должно ронять запрос (сброс пароля, приглашение); ссылку можно выдать повторно.
            log.exception("Не удалось отправить письмо на %s", to)


_mailer = SmtpMailer()


def get_mailer() -> Mailer:
    return _mailer
