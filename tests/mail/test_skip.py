"""What an email's headers and sender say about who wrote it, as evidence for the agent, which decides; and the
senders the owner listed, which alone are skipped without a run."""

import pytest

from glean.connectors.mail import message, skip

from . import samples

# The headers Outlook.com adds to what it receives, as its real mail has them, values shortened
JUNK = [
    ("X-MS-Exchange-Organization-SCL", "5"),
    ("X-Microsoft-Antispam-Mailbox-Delivery", "ucf:0;jmr:0;auth:1;dest:J;OFR:SpamFilterAuthJ;RF:JunkEmail;"),
]
BULK = [("X-Microsoft-Antispam", "BCL:5;PCL:0;RULEID:(5000109)(4604075)"), ("X-MS-Exchange-Organization-SCL", "1")]
PERSONAL = [("X-Microsoft-Antispam", "BCL:0;PCL:0;RULEID:(5000109)"), ("X-MS-Exchange-Organization-SCL", "1")]


def signals(raw, contact=False):
    return skip.signals(message.parse(raw), contact)


def test_personal_mail_says_nothing():
    assert signals(samples.plain()) == []
    assert signals(samples.plain(headers=PERSONAL)) == []


def test_mail_microsoft_filed_as_junk():
    assert signals(samples.plain(headers=JUNK)) == [
        "Microsoft's filter rated it spam (SCL 5)",
        "Microsoft's filter sent it to junk",
    ]


@pytest.mark.parametrize(
    "header, value",
    [("X-MS-Exchange-Organization-SCL", "6"), ("X-Forefront-Antispam-Report", "EFV:NLI;SFV:SPM;SCL:7;DIR:INB;")],
)
def test_a_high_spam_level(header, value):
    (said,) = signals(samples.plain(headers=[(header, value)]))
    assert said.startswith("Microsoft's filter rated it spam (SCL ")


def test_microsofts_bulk_rating():
    assert signals(samples.plain(headers=BULK)) == ["Microsoft rated it bulk mail (BCL 5)"]
    assert signals(samples.plain(headers=[("X-Microsoft-Antispam", "BCL:3;PCL:0")])) == []


@pytest.mark.parametrize(
    "header, value, said",
    [
        ("List-Unsubscribe", "<mailto:u@x.example>", "a List-Unsubscribe header, as mailing lists have"),
        ("List-Id", "Team <team.lists.example>", "a List-Id header, as mailing lists have"),
        ("Precedence", "Bulk", "Precedence: bulk"),
        ("Auto-Submitted", "auto-replied", "Auto-Submitted: auto-replied, as automatic replies and systems send"),
    ],
)
def test_bulk_and_automatic_headers(header, value, said):
    (found,) = signals(samples.plain(headers=[(header, value)]))
    assert found.startswith(said)


def test_harmless_values_of_those_headers():
    headers = [("Auto-Submitted", "no"), ("Precedence", "first-class"), ("X-MS-Exchange-Organization-SCL", "-1")]
    assert signals(samples.plain(headers=headers)) == []


@pytest.mark.parametrize(
    "sender",
    [
        "Shop <NoReply@shop.example>",
        "LinkedIn <security-noreply@linkedin.example>",
        "Alerts <alerts@exchange.example>",
        "Brand <info@newsletter.brand.example>",
    ],
)
def test_senders_no_person_answers_from(sender):
    (said,) = signals(samples.plain(sender=sender))
    assert "which no person answers from" in said


def test_role_addresses_that_people_answer_from_say_nothing():
    assert signals(samples.plain(sender="Support <support@shop.example>")) == []


def test_that_the_owner_wrote_to_the_sender_is_said_too():
    # A support desk's reply marked Auto-Submitted was a person's: the owner had written to it.
    found = signals(samples.plain(headers=[("Auto-Submitted", "auto-generated")]), contact=True)
    assert found[-1] == "the owner has written to its sender before"


def test_only_the_senders_the_owner_listed_are_skipped():
    listed = ("bot@*",)
    assert skip.reason(message.parse(samples.plain(sender="bot@x.example")), False, False, listed) == "sender: bot@*"
    assert skip.reason(message.parse(samples.plain(headers=JUNK + BULK[:1])), False, False, listed) is None
    assert skip.reason(message.parse(samples.plain(sender="Shop <noreply@shop.example>")), False, False, ()) is None


def test_what_the_owner_wrote_or_a_contact_sent_is_never_skipped():
    raw = message.parse(samples.plain(sender="bot@x.example"))
    assert skip.reason(raw, True, False, ("bot@*",)) is None
    assert skip.reason(raw, False, True, ("bot@*",)) is None
