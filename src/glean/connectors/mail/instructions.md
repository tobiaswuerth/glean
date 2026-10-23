## Importing an email

The email is a Markdown file: front matter with its headers (`from`, `to`, `cc`, `bcc`, `sender`, `reply_to`,
`date`, `subject`, `message_id`, `in_reply_to`, `references`, `direction`, `attachments`, `sha256`), then its text
as it came, quoted history and signatures included. `direction` is outbound when the owner wrote it.

The owner's addresses are {owner_addresses}: wherever one appears, that is Me, orbit's note of the owner, which links
name `Me`; never anyone else. If orbit lists one under `unknown_addresses`, add it to Me first (`upsert_person` with
`match: "Me"`). An address that carries the owner's name and acts as theirs (a school or work account, one
forwarding to them) is Me too: link it as Me, never as a new person, and propose a review of kind `address` with Me
as candidate and that address, which the owner confirms.

Worth recording, besides what the rules above say: an email the owner wrote to a person, however short (a line, a
link); a person's words a system brings, such as a support agent's reply in a ticket ("Aktualisiert von"), or a
person sharing something with the owner or inviting them through a platform (a workspace, a document, a calendar).
Skipped: notices of comments in a shared document (Google Docs, Dropbox Paper), whose conversation lives there.

1. **Read it all first**, decide whether it is worth recording, then tell the newest message apart from signatures,
   quoted history and forwarded parts, each signature tied to who wrote that part. The interaction is this email;
   its facts are those the newest message states. Quoted history was recorded with the email that said it, if orbit
   has it (its `thread` shows it). An email the owner forwards: what the owner adds is the newest message, and the
   forwarded part is context, its people mentioned only if the owner's words are about them.

2. **Log the interaction**, first, with `log_interaction`:
   - `kind`: "email"; `date`, `subject`, `direction`, `message_id`, `in_reply_to` and `references` from the front
     matter as they are; leave out those the front matter has as null, such as the subject of an email without one.
   - `sender`, `to` and `cc`: every address of the front matter's, as written there, however long the list; `orgs`:
     the organizations it concerns, by name. orbit links those it knows and lists the rest under
     `unknown_addresses` and `unknown_orgs`.
   - `source_file`: the Markdown file's path from the user message; `sha256`: the front matter's.
   - `language`; `summary`: what the newest message says, in a few sentences, in the email's language; `decisions`:
     those it states; `key_quote`: its sentence that matters most, word for word.
   - `documents`: the attachments saved for sift, each `{"title": its name, "document": its absolute path}`.

   `existing: true` means orbit has the email already (its `recorded` says what): answer `already_imported`, unless
   the user message says this is a retry; then add only what is missing. An email that answers one in orbit comes
   back with its `thread` (earlier interactions, their topics and open tasks): this email continues it.

3. **Resolve everyone it names**, all at once: every sender and recipient orbit didn't know (a class, a family or a
   team on one email are people the owner knows), people who sign or speak in it, people the newest message talks
   about, and their organizations (by domain, signature or text). Resolve a name with its organization too ("Hans
   Müller" with "Acme"): a name orbit didn't know may be a note's alias. Someone the newest message names without an
   address is a person to record when it says who they are (a role, an organization, a relation to someone); a first
   name alone is not enough. Role addresses (info@, support@, …) are not people, nor is a mailbox named after a
   place, a team or an office (zurich@shop.example, kundendienst@…), a mailing list, or the system a support desk or
   job platform sends from: they are the organization's, under `orgs`; whoever signs such a message is a person, at
   that organization. A person's name is the one the email writes (signature, greeting, display name), umlauts and
   all ("Müller", not "Mueller" from the address). An address orbit doesn't know and the email gives no name for (a
   class or list of bare addresses such as s1234@students.school.example) is no one to create: an address's handle is
   no name. Leave it unlinked, and list such addresses in the report. Free-mail domains are no organization; an
   organization's domain includes its subdomains (students.school.example is school.example's).

4. **Write**, each with the interaction's id as `source`:
   - `upsert_organization` first, then `upsert_person` (its `org` must exist): names, addresses, phones, job title
     and org as the email states them; a signature's details are its writer's. A person's `org` is the organization
     the email names for them; given none, orbit sets the one whose domain their address is at
     (`org_from_domain`). Someone writing through a marketplace or platform (an Airbnb host, a Ricardo seller) isn't
     the platform's. Set no field on a guess: no relationship (client, employer, …), domain or website the email
     doesn't state. A note named after an address handle, from before, whose real name the email gives: add the
     name to its `aliases` and say so in the report.
   - One `link_entities` call on the interaction for all its people and organizations: `from`, `to`, `cc`,
     `mentioned` (people the newest message talks about), `orgs`, `topics`; on each person `org`, and relations
     someone states (`reports_to`, `assistant_of`, `colleague_of`, `introduced_by`).
   - `add_observation` for lasting facts about the people, organizations and topics it concerns (a role, a move, a
     decision, an event someone invites the owner to); facts about the owner on Me. Not what is only passing (a
     meeting room, a parcel's whereabouts).
   - `set_fields` for a single value that changed (a new job title or phone number).
   - `create_task` for what the newest message leaves to be done, even a short reply's: what the owner promised,
     was asked or has to do (answer a question put to them, attend an interview or appointment, send or prepare
     something), status open, owner Me; and what the owner waits for from someone, promised or asked for (an answer,
     a document, a refund, a decision), status waiting, owner that person, or the organization when it is the
     company's to do (a shop's refund, a provider's fix). Due dates as stated, the words in `due_note`. An invitation
     is a task only if the owner must answer it. Each request or promise of the newest message is a task of its
     own, also in a thread with open tasks: never fold it into another task's note. When orbit refuses a task as
     like an open task of the thread, the email most likely changes that one: `update_task` it, or pass `match` if
     it is another.

5. **Topics and open tasks**: link the topic the email continues (a project, a deal, an application), found with
   `resolve_entity` of type topic or through the thread; create one only for what clearly runs over several emails.
   `log_interaction` lists the open tasks of the thread and of whom the email is with (`open_tasks`): each one this
   email settles (the refund came, the files arrived, the question was answered, the case was closed or cancelled)
   gets `update_task`, status done or cancelled, with a note.
