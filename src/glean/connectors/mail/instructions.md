## Importing an email

The email is a Markdown file: front matter with its headers (`from`, `to`, `cc`, `bcc`, `sender`, `reply_to`,
`date`, `subject`, `message_id`, `in_reply_to`, `references`, `direction`, `attachments`, `sha256`), then its text
as it came, quoted history and signatures included. `direction` is outbound when the owner wrote it.

The owner's addresses are {owner_addresses}: wherever one appears, that is Me, orbit's note of the owner, which links
name `Me`; never anyone else. If orbit lists one under `unknown_addresses`, add it to Me first (`upsert_person` with
`match: "Me"`). An address that carries the owner's name and acts as theirs (a school or work account, one
forwarding to them) is Me too: link it as Me, never as a new person, and propose a review of kind `address` with Me
as candidate and that address, which the owner confirms.

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
   job platform sends from: they are the organization's, under `orgs`; whoever signs such a message is a person. A
   person's name is one the email gives (signature, greeting, display name); a personal address's handle names
   someone only if nothing else does. Free-mail domains are no organization.

4. **Write**, each with the interaction's id as `source`:
   - `upsert_organization` first, then `upsert_person` (its `org` must exist): names, addresses, phones, job title
     and org as the email states them; a signature's details are its writer's. Set no field on a guess: no
     relationship (client, employer, …) the email doesn't state, and no `org` for someone whose organization it
     doesn't name (a school the owner attends is no employer).
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
     a document, a refund, a decision), status waiting, owner that person. Due dates as stated, the words in
     `due_note`. An invitation is a task only if the owner must answer it. When orbit refuses a task as like an open
     task of the thread, the email most likely changes that one: `update_task` it, or pass `match` if it is
     another.

5. **Topics and the thread's tasks**: link the topic the email continues (a project, a deal, an application),
   found with `resolve_entity` of type topic or through the thread; create one only for what clearly runs over several
   emails. Open tasks of the thread this email settles (an awaited answer, a promise kept): `update_task` with the new
   status and a note.
