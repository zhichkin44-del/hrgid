# Incident response for personal data incidents

Basis: Federal Law No. 152-FZ "On Personal Data", Art. 21 part 3.1 (as in force since 2023). This document is
a working one: the operator is the client company, HRgid is its tool; the operator is responsible for notification,
and the HRgid administrator helps to collect the facts. The legal qualification is decided by the owner
and the company's lawyer; this document is a procedure, not legal advice.

## What counts as an incident
An unlawful or accidental transfer (provision, distribution, access) of personal data that
violates the rights of data subjects: another person's dialogue shown to a different employee or company, a leak
of a database dump, a key or token in a public place, a model answer with someone else's data, loss of a data carrier.

## Deadlines (Art. 21 part 3.1)
| Deadline | What to do |
|---|---|
| 24 hours from detection | Notify Roskomnadzor: what happened, the presumed causes and the harm to data subjects' rights, the measures taken, the contact of the person responsible for the incident |
| 72 hours from detection | Submit the results of the internal investigation and details of the persons whose actions caused the incident |

The clock runs from the moment the event became known, not from the moment it happened. Record the time of detection.

## Procedure
1. **Stop (minutes).** Revoke the compromised key or token (model, Telegram, MCP, web chat invitations); if needed, stop the service: `docker compose stop api bot mcp`.
   Do not delete data: it is needed for the investigation.
2. **Record.** The time of detection, who noticed it and how, what and whose data could have been exposed. Save the logs:
   `docker compose logs --no-color > incident-<date>.log`, and the panel's sign-in and action journal (П10).
3. **Assess the scale.** Which companies (`tenant_id`), which categories of data, how many subjects. The traces
   store the masked text of questions (`traces.masked_question`), not the original: this narrows the assessment,
   but does not cancel it. Also check the masking label table: it lives only in the memory of the request.
4. **Notify within 24 hours** (operator -> Roskomnadzor, through the form on the agency's website; the HRgid administrator
   prepares the facts from items 2-3). In parallel, warn the affected employees and the company's HR.
5. **Fix the cause.** A fix and a test that reproduces the incident (an entry in
   `docs/threat-model.md`: threat -> test). Rotate the secrets: `HRGID_PANEL_PEPPER`, `HRGID_CODE_PEPPER`
   (after rotation all sign-in codes and invitations are issued again), the PostgreSQL password, the model keys.
6. **Submit within 72 hours** the results of the investigation and the measures.
7. **Review.** What did not work, what we change. The result is an ADR or an update of the threat model.

## Roles
| Role | Who | What they do |
|---|---|---|
| Person responsible for processing personal data | appointed by the operator company | decides on notification, signs |
| HRgid administrator | panel administrator | stops the services, collects the logs, prepares the facts |
| Project owner | by the owner's decision | legal assessment, contact with the lawyer |

## What to prepare in advance
- The contact of the responsible person in the company settings (panel, П9).
- A verified way to stop the services and revoke keys (the commands above).
- A database backup and the restore procedure: `docker compose exec postgres pg_dump -U hrgid hrgid`.
- The retention period for dialogues: 1 year by default (`HRGID_RETENTION_DAYS`, purged by `worker`).
