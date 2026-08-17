import logging

from discord_music_bot.log import RedactingFormatter


def test_formatter_redacts_credentials_and_signed_urls() -> None:
    token = "configured.discord.token"
    formatter = RedactingFormatter("%(message)s", secrets=(token,))
    record = logging.LogRecord(
        "test",
        logging.ERROR,
        __file__,
        1,
        (
            "token=%s Authorization: Bearer bearer-secret Cookie: session=cookie-secret "
            "stream=https://media.example/audio?sig=signed-secret"
        ),
        (token,),
        None,
    )

    output = formatter.format(record)

    assert token not in output
    assert "bearer-secret" not in output
    assert "cookie-secret" not in output
    assert "signed-secret" not in output
    assert output.count("<redacted>") == 4


def test_formatter_redacts_credentials_from_exception_text() -> None:
    token = "configured.discord.token"
    formatter = RedactingFormatter("%(message)s", secrets=(token,))

    try:
        raise RuntimeError(f"request failed with {token}")
    except RuntimeError:
        record = logging.LogRecord(
            "test",
            logging.ERROR,
            __file__,
            1,
            "failure",
            (),
            exc_info=__import__("sys").exc_info(),
        )

    output = formatter.format(record)

    assert token not in output
    assert "<redacted>" in output
