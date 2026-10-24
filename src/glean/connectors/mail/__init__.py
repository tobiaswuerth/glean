"""The mail connector: email from an IMAP server (Outlook.com and Hotmail, signed in with OAuth2), or from a folder
of saved .eml files. Every folder is read, except junk, trash and drafts; the mailbox is never changed."""

import email.utils
import hashlib
from dataclasses import replace
from datetime import datetime
from importlib.resources import files
from types import SimpleNamespace
from typing import ClassVar

from ... import archive, console
from ...state import Item
from ..base import Command, Connector, Downloaded
from . import markdown, message, oauth, options, skip
from .imap import Imap
from .mailbox import EmlFolder

UNSELECTABLE = {"\\noselect", "\\nonexistent"}
UNWANTED = {"\\junk", "\\trash", "\\drafts"}  # RFC 6154 special-use flags
PROGRESS_EVERY = 200  # messages between progress lines while downloading
RULE_REASONS = ("bulk:", "automated:", "sender:", "spam:")  # how skip_senders' reasons start, and older gleans' rules'
VERSION = "3"  # of the skip rules and documents: mail downloaded under another is brought in line, once
FREE_MAIL = frozenset(  # domains that say nothing about who works where
    "gmail.com googlemail.com outlook.com hotmail.com hotmail.ch hotmail.de live.com live.de msn.com icloud.com me.com "
    "yahoo.com yahoo.de aol.com proton.me protonmail.com gmx.ch gmx.net gmx.de web.de bluewin.ch sunrise.ch "
    "outlook.de t-online.de".split()
)


def _rechecked(item):
    """Whether the skip rules may judge an item again: no agent ran on it, and they or no one set it aside."""
    return not item.attempts and (item.status == "new" or (item.reason or "").startswith(RULE_REASONS))


GUIDE = f"""
glean reads your mail over IMAP, signed in with OAuth2: Microsoft no longer accepts passwords for IMAP. That needs
an app registered in Microsoft Entra, which takes a few minutes and is free; its Application (client) ID is what
glean asks for below. Each step is described at
{oauth.APP_GUIDE}

  1. https://entra.microsoft.com > App registrations > New registration
  2. Supported account types: "Personal Microsoft accounts only"
  3. Redirect URI: platform "Public client/native (mobile & desktop)", http://localhost
  4. Copy the Application (client) ID from its Overview page
"""


def _first(pool, count, newest):
    """The (folder name, ref) of the `count` oldest of these (folder name, Header), or the newest; all without a count.
    Those of unknown date come last. A copy of a message taken from another folder comes along without counting."""
    if count is None:
        return {(name, h.ref) for name, h in pool}
    dated = sorted((p for p in pool if p[1].received), key=lambda p: p[1].received, reverse=newest)
    taken, ids, counted = set(), set(), 0
    for name, header in dated + [p for p in pool if not p[1].received]:
        key = header.message_id and message.normalize_id(header.message_id)
        if not (key and key in ids):
            if counted == count:
                continue
            counted += 1
        taken.add((name, header.ref))
        if key:
            ids.add(key)
    return taken


def _login(connector, args):
    user = connector.account().login(device_code=args.device_code, say=connector.say)
    print(f"signed in as {user}")
    return 0


def _logout(connector, args):
    n = connector.account().logout()
    print(f"signed out: tokens of {console.count(n, 'account')} removed")
    return 0


class Mail(Connector):
    name = "mail"
    noun = "email"
    ignored = ("message.eml",)  # the originals: sift reads their Markdown documents instead
    contacts: frozenset = frozenset()  # the addresses the owner has written to, as the last upgrade found them
    help = "email: IMAP with OAuth2 (Outlook.com, Hotmail), or a folder of .eml files"
    commands: ClassVar[dict[str, Command]] = {
        "login": Command(
            "sign in to the mail account in the browser (OAuth2), once",
            _login,
            lambda p: p.add_argument(
                "--device-code", action="store_true", help="sign in on another device with a code, without a browser"
            ),
        ),
        "logout": Command("forget the mail account's tokens", _logout),
    }

    @classmethod
    def configure(cls, table):
        return options.configure(table)

    @classmethod
    def setup(cls, ask, say):
        say(GUIDE)
        address = ask.text("Your email address", check=lambda a: "@" in a and " " not in a)
        client_id = ask.text("Application (client) ID of your Entra app")
        others = ask.text("Your other email addresses, separated by commas (Enter: none)", default="")
        mine = [a.strip().lower() for a in others.split(",") if a.strip()]
        return options.template(address.lower(), client_id, mine)

    def after_setup(self, ask):
        if self.config.eml_folder is None and ask.yes("Sign in to your mail account now?", default=True):
            _login(self, SimpleNamespace(device_code=False))

    def account(self, app=None):
        return oauth.Account(self.config, self.home / f"tokens-{self.name}.bin", app)

    def mailbox(self):
        if self.config.eml_folder is not None:
            return EmlFolder(self.config.eml_folder)
        account = self.account()
        return Imap(self.config.host, self.config.port, self.config.address, account.token)

    def key(self, text):
        return message.normalize_id(text) or text

    def sender(self, author):
        """The first address of an email's From, and its domain unless that is a free-mail domain."""
        found = [a.casefold() for _, a in email.utils.getaddresses([author or ""]) if "@" in a]
        if not found:
            return None, None
        domain = found[0].rpartition("@")[2]
        return found[0], None if domain in FREE_MAIL else domain

    def check(self):
        if self.config.eml_folder is not None:
            ok = self.config.eml_folder.is_dir()
            return [
                (ok, f"mail: reads .eml files from {self.config.eml_folder}" + ("" if ok else ", which is missing"))
            ]
        try:
            user = self.account().signed_in()
        except Exception as e:  # a cache that can't be read is a problem to report, not a crash
            return [(False, f"mail: the token cache can't be read: {e}")]
        if user is None:
            return [(False, "mail: not signed in: run `glean mail login`")]
        return [(True, f"mail: signed in as {user}")]

    def chosen(self, folders):
        """The folders to read: those named in mail.folders, or else all but the excluded and those the server marks
        as junk, trash or drafts."""
        wanted = {f.lower() for f in self.config.folders}
        picked = []
        for folder in folders:
            if folder.flags & UNSELECTABLE:
                continue
            if wanted:
                if folder.name.lower() in wanted:
                    picked.append(folder)
            elif not folder.flags & UNWANTED and folder.name.lower() not in self.config.exclude_folders:
                picked.append(folder)
        return picked

    def download(self, state, oldest=None, latest=None):
        report = Downloaded()
        contacts = self._contacts(state)
        with self.mailbox() as box:
            scanned, folders = [], self.chosen(box.folders())
            self.say(f"looking for new {self.noun}s in {console.count(len(folders), 'folder')}")
            for folder in folders:
                headers, cursor = box.scan(folder, state.cursor(self.name, folder.name))
                scanned.append((folder, self._unknown(state, folder, headers, report), cursor))
            wanted = _first([(f.name, h) for f, todo, _ in scanned for h in todo], oldest or latest, bool(latest))
            for folder, todo, cursor in scanned:
                taken = [h for h in todo if (folder.name, h.ref) in wanted]
                report.waiting += len(todo) - len(taken)
                # a copy of a message stored from an earlier folder just now is recorded here too, not fetched again
                fetched = self._fetch(state, box, folder, self._unknown(state, folder, taken, report), report, contacts)
                if fetched and len(taken) == len(todo):  # else the next download scans this folder from here again
                    state.set_cursor(self.name, folder.name, cursor or "")
        if rescued := self._rescue(state, contacts):
            self.say(
                f"{console.count(rescued, 'skipped ' + self.noun)} from people you wrote to: waiting to be imported"
            )
        return report

    def _fetch(self, state, box, folder, todo, report, contacts):
        """Download these messages of a folder. False if some didn't arrive, so that the next download asks again."""
        if todo:
            self.say(f"{folder.name}: downloading {console.count(len(todo), self.noun)}")
        received = {h.ref: h.received for h in todo}
        arrived = set()
        for n, (ref, raw) in enumerate(box.fetch(folder, todo), 1):
            arrived.add(ref)
            stored = self._store(state, folder.name, ref, raw, received.get(ref), contacts)
            if stored is None:
                report.known += 1
            else:
                report.new += 1
                report.skipped += stored.status == "skipped"
                report.places[folder.name] = report.places.get(folder.name, 0) + 1
            if n % PROGRESS_EVERY == 0:
                self.say(f"{folder.name}: {n:,} of {len(todo):,}")
        if missing := len(todo) - len(arrived):
            self.say(f"{folder.name}: {console.count(missing, self.noun)} didn't arrive; the next download asks again")
            return False
        return True

    def upgrade(self, state):
        """Bring the mail downloaded before in line with the owner's addresses, once they change; and, once per
        version, apply the owner's skip_senders to the mail no agent ran on yet, so that what older gleans' own rules
        skipped is new again, and write the documents that mail downloaded before glean wrote them at once lacks."""
        self.contacts = self._contacts(state)  # for the documents' signals
        self._mine(state)
        if state.mark(self.name, "version") == VERSION:
            return
        touched = 0
        for item in state.items(self.name):
            if item.status == "failed" or not (_rechecked(item) or item.document is None):
                continue
            try:
                parsed = message.parse((self.archive / item.path).read_bytes())
            except Exception:  # an original gone or unreadable stays as it is
                continue
            before = item
            if _rechecked(item):
                item = self._rules(state, item, parsed)
            if item.document is None:
                item = self._write_document(state, item, parsed)
            touched += item != before
        state.set_mark(self.name, "version", VERSION)
        if touched:
            self.say(f"brought {console.count(touched, self.noun)} downloaded before in line with this glean's rules")

    def _mine(self, state):
        """Bring the mail downloaded before in line with the owner's addresses, once they change: what was sent from
        an address added is theirs (outbound), its recipients contacts, and no skip rule sets it aside; what was sent
        from one taken out is someone else's again. Its document says so."""
        mine = ",".join(sorted(self.config.my_addresses))
        if state.mark(self.name, "my_addresses") == mine:
            return
        turned = 0
        for item in state.items(self.name):
            senders = {address.lower() for _, address in email.utils.getaddresses([item.author])}
            direction = "outbound" if senders & set(self.config.my_addresses) else "inbound"
            if item.status == "failed" or direction == item.direction:
                continue
            item, turned = state.set_direction(item, direction), turned + 1
            try:
                parsed = message.parse((self.archive / item.path).read_bytes())
            except Exception:  # an original gone or unreadable: its direction is all that changes
                continue
            if direction == "outbound":
                state.add_contacts(self.name, self._recipients(parsed))
                self.contacts |= set(self._recipients(parsed))
            if _rechecked(item):
                item = self._rules(state, item, parsed)
            if item.document:
                self._write_document(state, item, parsed)
        state.set_mark(self.name, "my_addresses", mine)
        if turned:
            self.say(f"brought {console.count(turned, self.noun)} downloaded before in line with your addresses")

    def _rules(self, state, item, parsed):
        """Apply the skip rules to an item again; returns it as it is now."""
        contact = any(a.email in self.contacts for a in parsed.from_)
        why = skip.reason(parsed, item.direction == "outbound", contact, self.config.skip_senders)
        if (item.status, item.reason) != ("skipped" if why else "new", why):
            item = state.set_status(item, "skipped" if why else "new", why)
        return item

    def _contacts(self, state):
        """The addresses the owner has written to, which skip rules never apply to. Kept as outbound mail is stored,
        and gathered once from the outbound mail glean downloaded before it kept them."""
        if not state.contacts(self.name):
            for item in state.items(self.name):
                if item.direction == "outbound":
                    try:
                        parsed = message.parse((self.archive / item.path).read_bytes())
                    except Exception:  # an original gone or unreadable names no contacts
                        continue
                    state.add_contacts(self.name, self._recipients(parsed))
        return state.contacts(self.name)

    def _recipients(self, parsed):
        return [a.email for a in (*parsed.to, *parsed.cc, *parsed.bcc) if a.email not in self.config.my_addresses]

    def _rescue(self, state, contacts):
        """Let back in what the skip rules set aside before its sender turned out to be someone the owner wrote to."""
        rescued = 0
        for item in state.items(self.name, "skipped"):
            senders = {address.lower() for _, address in email.utils.getaddresses([item.author])}
            if item.reason and item.reason.startswith(RULE_REASONS) and senders & contacts:
                self._write_document(state, state.set_status(item, "new"))
                rescued += 1
        return rescued

    def _unknown(self, state, folder, headers, report):
        """The headers of messages the state database doesn't know. One it knows from another folder, by its
        Message-ID, is recorded in this one too, without downloading it again."""
        known, todo = state.known_refs(self.name, folder.name), []
        for header in headers:
            if header.ref in known:
                continue
            seen = header.message_id and state.find(self.name, message.normalize_id(header.message_id))
            if seen:
                state.locate(seen, folder.name, header.ref)
                self._refresh(state, seen)
                report.known += 1
            else:
                todo.append(header)
        return todo

    def _store(self, state, folder, ref, raw, received, contacts):
        """Store one downloaded message: its original in the archive, its item in the state database, and its
        document unless it is spam. None if the state database knows it already (found in another folder, or before
        its folder's UIDVALIDITY changed). A message that can't be read, or the notice a server sent in its place, is
        kept too, as failed, so that it doesn't stop every later download and no run takes it."""
        digest = hashlib.sha256(raw).hexdigest()
        try:
            parsed = message.parse(raw)
            broken = message.stand_in(parsed)
        except Exception as e:  # a bug of the email package, met by a malformed message
            parsed, broken = None, f"it couldn't be read: {type(e).__name__}: {e}"
        key = parsed.message_id if parsed else f"{digest[:32]}@glean.invalid"
        seen = state.find(self.name, key)
        if seen:
            state.locate(seen, folder, ref)
            self._refresh(state, seen)
            return None
        date = parsed and parsed.date
        when = archive.local_time(date or received or datetime.now().astimezone())
        item_id = state.new_id(self.name, key)
        original = archive.item_folder(self.archive, when, item_id) / markdown.ORIGINAL
        archive.write_atomic(original, raw)
        outbound = bool(parsed) and any(a.email in self.config.my_addresses for a in parsed.from_)
        if outbound:
            state.add_contacts(self.name, self._recipients(parsed))
            contacts |= set(self._recipients(parsed))
        contact = bool(parsed) and any(a.email in contacts for a in parsed.from_)
        why = broken or skip.reason(parsed, outbound, contact, self.config.skip_senders)
        item = Item(
            source=self.name,
            key=key,
            date=(date or when).isoformat(),
            title=parsed.subject if parsed else "(unreadable)",
            author=", ".join(str(a) for a in parsed.from_) if parsed else "",
            container=folder,
            direction="outbound" if outbound else "inbound",
            path=archive.relative(self.archive, original),
            sha256=digest,
            size=len(raw),
            status="failed" if broken else "skipped" if why else "new",
            reason=why,
            id=item_id,
        )
        stored = state.add(item, folder, ref)
        if not broken:
            stored = self._write_document(state, stored, parsed)
        return stored

    def _write_document(self, state, item, parsed=None):
        """Write an item's document while downloading or upgrading; one that fails is said, not raised (its run
        writes it again), so that it doesn't stop the others."""
        try:
            if parsed is None:
                parsed = message.parse((self.archive / item.path).read_bytes())
            document = markdown.write(parsed, item, (self.archive / item.path).parent, self.config.save_attachments)
        except (OSError, ValueError) as e:
            self.say(f"warning: the document of {item.id} ({item.title[:60]}) couldn't be written: {e}")
            return item
        return state.set_document(item, archive.relative(self.archive, document.markdown))

    def _refresh(self, state, item):
        """Write a document again that lists where its email is found, now that it is found in one more place."""
        if item.document:
            self._write_document(state, state.find(self.name, item.key))

    def document(self, item):
        original = self.archive / item.path
        parsed = message.parse(original.read_bytes())
        written = markdown.write(parsed, item, original.parent, self.config.save_attachments)
        outbound = any(a.email in self.config.my_addresses for a in parsed.from_)
        contact = any(a.email in self.contacts for a in parsed.from_)
        signals = () if outbound else tuple(skip.signals(parsed, contact))  # what the owner wrote is theirs
        addresses = tuple(dict.fromkeys(a.email for a in (*parsed.from_, *parsed.to, *parsed.cc) if "@" in a.email))
        return replace(
            written, related=self.related(parsed, self.config.my_addresses), signals=signals, addresses=addresses
        )

    @staticmethod
    def related(parsed, mine):
        """What an email shares with others that mustn't be imported at the same time, lest two runs create the same
        person or organization: its correspondents' addresses, their company's domain, and its thread."""
        keys = set()
        for address in (*parsed.from_, *parsed.to, *parsed.cc, *parsed.bcc, *parsed.reply_to):
            if address.email in mine or "@" not in address.email:
                continue
            keys.add(f"address:{address.email}")
            if (domain := address.email.rpartition("@")[2]) not in FREE_MAIL:
                keys.add(f"domain:{domain}")
        root = parsed.references[0] if parsed.references else parsed.in_reply_to or parsed.message_id
        return frozenset({*keys, f"thread:{root}"})

    def instructions(self):
        return self._prompt("instructions.md")

    def triage_instructions(self):
        return self._prompt("triage.md")

    def _prompt(self, name):
        text = files(__package__).joinpath(name).read_text(encoding="utf-8")
        *others, last = self.config.my_addresses  # the account's address at least
        return text.replace("{owner_addresses}", f"{', '.join(others)} and {last}" if others else last)
