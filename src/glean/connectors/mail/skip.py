"""What an email's headers and sender say about whether a person wrote it: Microsoft's spam and bulk ratings,
mailing-list and automatic headers, a sender no person answers from, and whether the owner wrote to the sender before.
They are evidence for the agent, which decides: filters err, and people write through mailing lists and ticket
systems (of 1,798 emails such rules skipped, the replies of a support desk and an invitation Microsoft rated spam were
people's). Only the senders the owner lists in skip_senders are skipped without an agent run."""

import re
from fnmatch import fnmatchcase

BULK_PRECEDENCE = ("bulk", "list", "junk")
SPAM_LEVEL = 5  # Microsoft's spam confidence level from which it files mail as junk
BULK_LEVEL = 4  # Microsoft's bulk complaint level from which mail is a mass mailing: newsletters, alerts, shop mail
SCL = re.compile(r"(?:^|;)\s*SCL:(-?\d+)", re.IGNORECASE)
BCL = re.compile(r"(?:^|;)\s*BCL:(\d+)", re.IGNORECASE)
MACHINES = (  # senders no person answers from
    "*noreply*@*",
    "*no-reply*@*",
    "*no_reply*@*",
    "*donotreply*@*",
    "*do-not-reply*@*",
    "*do_not_reply*@*",
    "mailer-daemon@*",
    "postmaster@*",
    "bounce*@*",
    "notifications@*",
    "notification@*",
    "notify@*",
    "alerts@*",
    "alert@*",
    "automated@*",
    "newsletter@*",
    "newsletters@*",
    "*@newsletter.*",
    "*@notifications.*",
    "*@mailing.*",
)


def microsoft(headers):
    """Microsoft's verdicts on a mail it received: (spam level, bulk level, filed as junk), None where it gave none."""
    scl = headers.get("x-ms-exchange-organization-scl", "").strip()
    report = SCL.search(headers.get("x-forefront-antispam-report", ""))
    spam = int(scl) if re.fullmatch(r"-?\d+", scl) else int(report.group(1)) if report else None
    bulk = BCL.search(headers.get("x-microsoft-antispam", ""))
    delivery = headers.get("x-microsoft-antispam-mailbox-delivery", "")
    junk = bool(re.search(r"(?:^|;)\s*dest:J\s*(?:;|$)", delivery)) or "RF:JunkEmail" in delivery
    return spam, int(bulk.group(1)) if bulk else None, junk


def signals(message, contact=False):
    """What an email's headers and sender say, each as a phrase for the agent: the signs of automated mail, and that
    the owner wrote to the sender before."""
    headers, found = message.headers, []
    spam, bulk, junk = microsoft(headers)
    if spam is not None and spam >= SPAM_LEVEL:
        found.append(f"Microsoft's filter rated it spam (SCL {spam})")
    if junk:
        found.append("Microsoft's filter sent it to junk")
    if bulk is not None and bulk >= BULK_LEVEL:
        found.append(f"Microsoft rated it bulk mail (BCL {bulk})")
    for name, title in (("list-unsubscribe", "List-Unsubscribe"), ("list-id", "List-Id")):
        if name in headers:
            found.append(f"a {title} header, as mailing lists have")
    if (precedence := headers.get("precedence", "").strip().lower()) in BULK_PRECEDENCE:
        found.append(f"Precedence: {precedence}")
    if (auto := headers.get("auto-submitted", "").strip().lower()) and auto != "no":
        found.append(f"Auto-Submitted: {auto}, as automatic replies and systems send, ticket systems too")
    senders = [(a.email, _machine(a.email)) for a in message.from_]
    found += [f"its sender {email} is like {like}, which no person answers from" for email, like in senders if like]
    if contact:
        found.append("the owner has written to its sender before")
    return found


def _machine(address):
    """The pattern of MACHINES the address is like, or None."""
    return next((p for p in MACHINES if fnmatchcase(address, p)), None)


def reason(message, outbound, contact, senders):
    """Why an email is skipped without an agent run: its sender is one the owner listed (`senders`, patterns in
    lower case); None for every other, and for what the owner wrote or a contact sent."""
    if outbound or contact:
        return None
    for address in message.from_:
        for pattern in senders:
            if fnmatchcase(address.email, pattern):
                return f"sender: {pattern}"
    return None
