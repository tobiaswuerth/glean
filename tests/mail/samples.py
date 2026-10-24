"""Synthetic emails for the tests, as raw bytes the way servers deliver them. No real mail."""

from email.message import EmailMessage
from email.policy import SMTP

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def plain(
    *,
    message_id="<CAF1234@mail.acme.example>",
    sender="Hans Müller <hans.mueller@acme.example>",
    to="John Doe <john@example.com>",
    subject="Offerte Wartungsvertrag 2027",
    date="Wed, 14 Oct 2026 09:32:00 +0200",
    body="Grüezi Herr Doe\n\nAnbei die Offerte.\n\nFreundliche Grüsse\nHans Müller\nAcme AG\n",
    headers=(),
):
    m = EmailMessage()
    if message_id:
        m["Message-ID"] = message_id
    m["From"] = sender
    m["To"] = to
    m["Subject"] = subject
    if date:
        m["Date"] = date
    for name, value in headers:
        m[name] = value
    m.set_content(body)
    return m.as_bytes(policy=SMTP)


def with_attachments():
    m = EmailMessage()
    m["Message-ID"] = "<att-1@acme.example>"
    m["From"] = "hans.mueller@acme.example"
    m["To"] = "john@example.com"
    m["Cc"] = "Lea Frei <lea.frei@acme.example>, info@acme.example"
    m["Subject"] = "Unterlagen"
    m["Date"] = "Thu, 15 Oct 2026 14:05:00 +0200"
    m["In-Reply-To"] = "<CAF1234@mail.acme.example>"
    m["References"] = "<first@x.example> <CAF1234@mail.acme.example>"
    m.set_content("Plain body\n")
    m.add_alternative("<html><body><p>HTML body</p><img src='cid:logo'></body></html>", subtype="html")
    m.get_payload()[1].add_related(PNG, "image", "png", cid="<logo>", filename="logo.png")
    m.add_attachment(PDF, maintype="application", subtype="pdf", filename="Offerte 2027.pdf")
    m.add_attachment(PNG, maintype="image", subtype="png", filename="Foto.png")
    m.add_attachment(b"col1,col2\n", maintype="text", subtype="csv", filename="data.csv")
    m.add_attachment(PDF, maintype="application", subtype="pdf", filename="Offerte 2027.pdf")  # same name twice
    return m.as_bytes(policy=SMTP)


def html_only():
    m = EmailMessage()
    m["Message-ID"] = "<html-1@shop.example>"
    m["From"] = "Shop <news@shop.example>"
    m["To"] = "john@example.com"
    m["Subject"] = "Newsletter"
    m["Date"] = "Fri, 16 Oct 2026 08:00:00 +0000"
    m["List-Unsubscribe"] = "<mailto:unsubscribe@shop.example>"
    html = (
        "<html><head><style>p {color: red}</style><script>alert(1)</script></head><body>"
        "<h1>Angebote</h1><table><tr><td>Preis</td><td>CHF 10</td></tr></table>"
        "<ul><li>Eins</li><li>Zwei</li></ul><p>Mehr <a href='https://shop.example/x'>hier</a></p></body></html>"
    )
    m.set_content(html, subtype="html")
    return m.as_bytes(policy=SMTP)


def forwarded():
    inner = EmailMessage()
    inner["From"] = "Lea Frei <lea.frei@acme.example>"
    inner["To"] = "hans.mueller@acme.example"
    inner["Subject"] = "Termin"
    inner["Date"] = "Mon, 12 Oct 2026 10:00:00 +0200"
    inner.set_content("Passt Dienstag?\n")
    outer = EmailMessage()
    outer["Message-ID"] = "<fwd-1@acme.example>"
    outer["From"] = "hans.mueller@acme.example"
    outer["To"] = "john@example.com"
    outer["Subject"] = "WG: Termin"
    outer["Date"] = "Tue, 13 Oct 2026 11:00:00 +0200"
    outer.set_content("Siehe unten.\n")
    outer.add_attachment(inner)
    return outer.as_bytes(policy=SMTP)


ENCODED = (
    b"Message-ID: <ENC-1@Example.COM>\r\n"
    b"From: =?utf-8?q?J=C3=BCrg_M=C3=BCller?= <juerg@example.ch>\r\n"
    b"To: undisclosed-recipients:;\r\n"
    b"Subject: =?iso-8859-1?q?Gr=FCsse_aus_Z=FCrich?=\r\n"
    b"Date: 14 Oct 2026 07:00:00 -0000\r\n"
    b"Sender: Assistentin <assist@example.ch>\r\n"
    b"Reply-To: Team <team@example.ch>\r\n"
    b"MIME-Version: 1.0\r\n"
    b"Content-Type: text/plain; charset=iso-8859-1\r\n"
    b"Content-Transfer-Encoding: quoted-printable\r\n"
    b"\r\n"
    b"Gr=FCezi mitenand\r\n"
)

BROKEN = (
    b"From: nobody\r\n"
    b"To: <<<\r\n"
    b"Subject: Kaputt\r\n"
    b"Date: not a date\r\n"
    b"Content-Type: text/plain; charset=x-unknown-charset\r\n"
    b"\r\n"
    b"Text \xe4\r\n"
)

APPLE_SPLIT = (
    b"Message-ID: <split@example.com>\r\n"
    b"From: a@example.com\r\n"
    b"To: john@example.com\r\n"
    b"Subject: Split body\r\n"
    b"Date: Wed, 14 Oct 2026 09:32:00 +0200\r\n"
    b"MIME-Version: 1.0\r\n"
    b'Content-Type: multipart/mixed; boundary="b1"\r\n'
    b"\r\n"
    b"--b1\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"Before the file.\r\n"
    b"--b1\r\n"
    b"Content-Type: application/pdf; name=a.pdf\r\n"
    b"Content-Disposition: inline; filename=a.pdf\r\n"
    b"Content-Transfer-Encoding: base64\r\n"
    b"\r\n"
    b"JVBERi0xLjQK\r\n"
    b"--b1\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"After the file.\r\n"
    b"--b1--\r\n"
)

EMPTY_PLAIN = (
    b"Message-ID: <alt@example.com>\r\n"
    b"From: a@example.com\r\n"
    b"Subject: Alternative\r\n"
    b"Date: Wed, 14 Oct 2026 09:32:00 +0200\r\n"
    b"MIME-Version: 1.0\r\n"
    b'Content-Type: multipart/alternative; boundary="b2"\r\n'
    b"\r\n"
    b"--b2\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"  \r\n"
    b"--b2\r\n"
    b'Content-Type: multipart/related; boundary="b3"\r\n'
    b"\r\n"
    b"--b3\r\n"
    b"Content-Type: text/html; charset=utf-8\r\n"
    b"\r\n"
    b"<p>Only in HTML</p>\r\n"
    b"--b3--\r\n"
    b"--b2--\r\n"
)
