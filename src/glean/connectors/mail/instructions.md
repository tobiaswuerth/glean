## Importing an email

The email is a Markdown file: front matter with its headers (`from`, `to`, `cc`, `bcc`, `sender`, `reply_to`,
`date`, `subject`, `message_id`, `in_reply_to`, `references`, `direction`, `attachments`, `sha256`), then its text
as it came, quoted history and signatures included. `direction` is outbound when the owner wrote it.

The owner's addresses are {owner_addresses}, and every other address orbit knows as Me's (`log_interaction` links it
as Me, `resolve_entity` returns Me): wherever one appears, that is Me, orbit's note of the owner, which links name
`Me`; never anyone else. If orbit lists one of the owner's under `unknown_addresses`, add it to Me first
(`upsert_person`, `match: "Me"`). An address that carries the owner's name and acts as theirs (a school or work account, a forward) is
Me too: link it as Me, and propose a review of kind `address` (candidate Me, that address) for the owner to confirm.

1. **Read it all.** Tell the newest message from signatures, quoted history and forwarded parts, and each signature
   from who wrote that part. The interaction is this email; its facts are what the newest message states. Quoted
   history belongs to the email that said it (orbit's `thread` shows those it has). In a forward, the owner's words
   are the newest message; the forwarded part is context, its people mentioned only if the owner's words are about
   them.

2. **Log it** with `log_interaction`:
   - `kind` "email"; `date`, `subject`, `direction`, `message_id`, `in_reply_to` and `references` from the front
     matter as they are; leave out those the front matter has as null, such as the subject of an email without one.
   - `sender`, `to` and `cc`: every address of the front matter, the owner's too, as written, however long the list;
     `orgs`: the
     organizations it concerns, by name. orbit links those it knows and lists the rest under `unknown_addresses` and
     `unknown_orgs`.
   - `source_file`: the Markdown file's path from the user message; `sha256`: the front matter's.
   - `language`; `summary`: what the newest message says, in a few sentences, in its language; `decisions` it states;
     `key_quote`: its sentence that matters most, word for word.
   - `documents`: the attachments saved for sift, each `{"title": its name, "document": its absolute path}`.

   `existing: true`: orbit has it already, and `recorded` says what. Answer `already_imported`, unless the user
   message says this is a retry: then add what is missing. A reply comes back with its `thread` (earlier
   interactions, their topics and open tasks): this email continues it.

3. **Resolve everyone**, in one turn: every sender and recipient orbit didn't know, whoever signs or speaks, the people
   the newest message talks about, and their organizations (by domain, signature or text). Resolve a name with its
   organization ("Hans Müller" with "Acme"): it may be a note's alias.
   - Every sender and recipient who is a person is recorded, a bare address too. Their name is the one the email
     writes (signature, greeting, display name), umlauts and all ("Müller", not "Mueller"); else the one the address
     spells, with or without dots and digits (hans.mueller@…, hansmueller7@…: Hans Mueller); else the address's
     handle (s1234), which a later email's name replaces.
   - Someone named without an address is recorded when the email says who they are (a role, an organization, a
     relation); a first name alone is not enough.
   - Not people: role addresses (info@, support@, …), a mailbox named after a place, a team or an office
     (zurich@shop.example, kundendienst@…), a mailing list, the system a support desk or job platform sends from.
     They are the organization's, under `orgs`; whoever signs such a message is a person, at that organization, and
     the address stays off their note: link them by name.
   - Free-mail domains are no organization; an organization's domain includes its subdomains
     (students.school.example is school.example's).

4. **Write**, each with the interaction's id as `source`:
   - **People and organizations**: `upsert_organization` first, then `upsert_person`, with names, addresses, phones,
     job title and org as the email states them; a signature's details are its writer's. A person's `org` is the one
     the email names; given none, orbit sets the one their address's domain belongs to (`org_from_domain`). Someone
     writing through a marketplace or platform (an Airbnb host, a seller) isn't the platform's. No field on a guess:
     no relationship (client, employer, …), domain or website the email doesn't state. A note named after a handle
     whose real name the email gives: add the name to its `aliases`, and say so in the report.
   - **Links**: one `link_entities` call on the interaction for `from`, `to`, `cc`, `mentioned` (people the newest
     message talks about), `orgs` and `topics`; on each person `org`, and the relations someone states
     (`reports_to`, `assistant_of`, `colleague_of`, `introduced_by`).
   - **Facts**: `add_observation` for lasting facts about the people, organizations and topics (a role, a move, a
     decision, an event someone invites the owner to), and about the owner on Me; not what is passing (a meeting
     room, a parcel's whereabouts). `set_fields` for a single value that changed (a new job title or phone).
   - **Open tasks, before new ones**: `log_interaction` lists the open tasks of the thread and of whom the email is with
     (`open_tasks`). Every one this email settles gets `update_task`, status done or cancelled, with a note: the answer,
     document, refund or decision came; a reply or a thank-you shows the owner did it; a later step shows it took place
     (an offer after the interview, a follow-up after the call); the problem was solved or the case closed; it was
     declined or no longer applies. One this email changes (a new date, more to do) gets `update_task` too.
   - **Tasks**: then `create_task` for each new request or promise of the newest message, even a short reply's. What the
     owner promised, was asked or has to do (answer a question, attend an interview or appointment, send or prepare
     something): status open, owner Me. What the owner waits for (an answer, a document, a refund, a decision): status
     waiting, owner that person, or the organization when it is the company's to do (a refund, a fix). Due dates as
     stated, the words in `due_note`. An invitation is a task only if the owner must answer it. Each is a task of its
     own, also in a thread with open tasks: never fold it into another's note, but for one case: vacancies or offers
     someone proposes unasked (a recruiter's list, one per email) make one task per sender and day (the email's), to
     tell them which interest the owner; if `open_tasks` holds it, add this one to its note with `update_task`. If orbit
     refuses a task as like an open task of the thread, the email likely changes that one, or is a copy of the email
     that made it: `update_task` it, or pass `match` if it is another.

5. **Topics**: link the topic the email continues (a project, a deal, an application), found with `resolve_entity` of
   type topic or through the thread; create one only for what clearly runs over several emails.
