"""A fake IMAP server with imaplib's interface and response shapes, which records every command sent to it."""

import imaplib
import re

READ_ONLY = {"AUTHENTICATE", "LIST", "EXAMINE", "UID SEARCH", "UID FETCH", "LOGOUT"}


class FakeImap:
    def __init__(self, folders, *, uid_last=False, refuse_auth=False, refuse=()):
        # folders: {name: {"flags": "\\Sent", "validity": 14, "messages": {uid: raw}, "wire": "..."}}
        self.folders, self.uid_last, self.refuse_auth, self.refuse = folders, uid_last, refuse_auth, set(refuse)
        self.commands, self.selected, self.untagged, self.auth = [], None, {}, None
        self.no_uid, self.no_body = set(), set()  # responses glean can't place; messages the server leaves out
        self.undated = set()  # messages whose INTERNALDATE the server leaves out

    def __call__(self, host, port, timeout=None):  # stands in for imaplib.IMAP4_SSL
        self.address = (host, port, timeout)
        return self

    def authenticate(self, mechanism, callback):
        self.commands.append("AUTHENTICATE")
        self.auth = (mechanism, callback(b""))
        if self.refuse_auth:
            raise imaplib.IMAP4.error("AUTHENTICATE failed.")
        return "OK", [b"Authenticated"]

    def list(self):
        self.commands.append("LIST")
        if "LIST" in self.refuse:
            return "NO", [b"LIST failed"]
        lines = [None]  # imaplib gives None for an empty response line; glean skips it
        for name, f in self.folders.items():
            wire = f.get("wire", name)
            flags = f"(\\HasNoChildren {f.get('flags', '')})".replace(" )", ")").encode()
            if " " in wire or f.get("literal"):
                if f.get("literal"):
                    lines.append((flags + b' "/" {%d}' % len(wire), wire.encode()))
                else:
                    lines.append(flags + b' "/" "' + wire.encode() + b'"')
            else:
                lines.append(flags + b' "/" ' + wire.encode())
        lines.append(b"garbage that is no LIST line")
        return "OK", lines

    def select(self, mailbox, readonly=False):
        assert readonly, "glean must open folders read-only (EXAMINE)"
        self.commands.append("EXAMINE")
        name = mailbox.strip('"').replace('\\"', '"')
        folder = next((n for n, f in self.folders.items() if f.get("wire", n) == name), None)
        if folder is None or "EXAMINE" in self.refuse:
            return "NO", [b"no such folder"]
        self.selected = folder
        if self.folders[folder]["validity"] is not None:
            self.untagged["UIDVALIDITY"] = [str(self.folders[folder]["validity"]).encode()]
        return "OK", [str(len(self.folders[folder]["messages"])).encode()]

    def response(self, code):
        return code, self.untagged.pop(code, [None])

    def uid(self, command, *args):
        self.commands.append(f"UID {command}")
        messages = self.folders[self.selected]["messages"]
        if command == "SEARCH":
            low = int(re.match(r"UID (\d+):\*", args[1]).group(1))
            found = [u for u in sorted(messages) if u >= low] or sorted(messages)[-1:]  # n:* matches the last one
            return "OK", [" ".join(map(str, found)).encode()]
        assert command == "FETCH"
        uids, items = args
        assert "BODY[" not in items.replace("BODY.PEEK[", ""), "glean must fetch with BODY.PEEK"
        data = []
        for n, uid in enumerate(map(int, uids.split(",")), 1):
            if uid not in messages:
                continue
            raw = messages[uid]
            if uid in self.no_body and "HEADER.FIELDS" not in items:
                continue
            if uid in self.no_uid:  # a response glean can't place
                data += [(b"%d (BODY[] {%d}" % (n, len(raw)), raw), b")"]
            elif "HEADER.FIELDS" in items:
                header = b"".join(re.findall(rb"(?im)^message-id:.*\r?\n", raw)) + b"\r\n"
                meta = b"%d (UID %d RFC822.SIZE %d " % (n, uid, len(raw))
                meta += b"" if uid in self.undated else b'INTERNALDATE " 4-Oct-2026 09:32:00 +0200" '
                data += [(meta + b"BODY[HEADER.FIELDS (MESSAGE-ID)] {%d}" % len(header), header), b")"]
            elif self.uid_last:
                data += [(b"%d (BODY[] {%d}" % (n, len(raw)), raw), b" UID %d)" % uid]
            else:
                data += [(b"%d (UID %d BODY[] {%d}" % (n, uid, len(raw)), raw), b")"]
        return "OK", data

    def logout(self):
        self.commands.append("LOGOUT")
        return "BYE", [b"bye"]
