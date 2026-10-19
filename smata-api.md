# SMATA API reference (unofficial)

SMATA does not publish an API. This document was reverse-engineered on 2026-10-06 from two sources:

1. The JavaScript bundles of the SMATA web app. Every HTTP call the app makes was extracted, together with the parameters it sends.
2. Read-only `GET` requests made with a **lot owner** account.

It is incomplete and may go out of date without notice. Write endpoints (`POST`/`PUT`/`PATCH`/`DELETE`) are listed only so you know what exists. **None of them were called.**

**Status legend:**
- ✅ **Verified.** Returned `200` for an owner account. The response shape below comes from real responses.
- 🔒 **Restricted.** Exists, but returns `403` or `404` for an owner. It is meant for staff or strata-manager roles.
- ✏️ **Write.** Seen in the web app's code. Not exercised.
- 🔍 **Unverified read.** Seen in the code but not called. Its parameters are inferred.

---

## 1. Hosts

- `https://<tenant>.my.smata.com`: the server-rendered Rails app. It serves the login form and the legacy pages (`/jobs`, `/documents`). `<tenant>` is the strata manager's subdomain.
- `https://<tenant>.app.smata.com`: the newer Next.js single-page app. It calls the JSON API below.
- **`https://my.smata.com/api`: the JSON API.** Every path in this document is relative to this base.
- `https://s3.smata.com/uploads/...`: file storage. **These URLs download without authentication.**
- `https://smata-production-v2.s3.ap-southeast-2.amazonaws.com/...`: presigned S3 URLs (`links.file_url_v2`). They expire after 900 seconds.

---

## 2. Authentication

There is no API-key or OAuth flow for owners. The session is created by the web login form:

1. `GET https://<tenant>.my.smata.com/users/sign_in`
   - Parse `<input name="authenticity_token" value="...">` (a Rails CSRF token).
2. `POST https://<tenant>.my.smata.com/users/sign_in`, sent as `application/x-www-form-urlencoded`, with these form fields:
   - `utf8`: `✓`
   - `authenticity_token`: the token from step 1
   - `user[email]`: the login email
   - `user[password]`: the password
   - `user[remember_me]`: `0`
   - `commit`: `Log in`
3. A successful login sets an **`access_token` cookie**. Send it on every API call as a header:
   ```
   Authorization: Bearer <access_token>
   Accept: application/json
   ```

A failed login returns `200` with the login page again, and no `access_token` cookie. Check for the cookie rather than the status code.

The token's lifetime and refresh behaviour are unknown. Logging in again works.

The web app also has `PUT /v1/users/{id}` with a `two_fa_enabled` field. Accounts with two-factor authentication turned on will need an extra step that isn't documented here.

---

## 3. Conventions

### Pagination
- Request with `page` (1-based) and `per_page`. The defaults vary: 20, 25 or 50.
- List responses include:
  ```json
  "meta": { "total_count": 0, "page": 1, "per_page": 50, "total_pages": 1 }
  ```
- Some lists also carry extras in `meta`, such as `favorites`, `my_tasks`, `unseen_documents` and `unread_chat_rooms_ids`.
- A few endpoints return a bare JSON array with no `meta`: `locations`, `location_contacts`, `building_profile/site_plans`, `building_contacts`, `digital_noticeboards`.

### Data types
- **Timestamps** are Unix epoch **seconds** (integers), e.g. `created_at`, `added_date`, `meeting_date`.
- **`file_size`** is a human-readable string, e.g. `"356 KB"`, not a number of bytes.
- **`time_zone`** (an IANA name, e.g. `Australia/Sydney`) is sent on document list calls. It affects date filtering.

### Request encoding
- **Array parameters** use Rails bracket style: `site_plan_ids[]=A&site_plan_ids[]=B`.
- **Nested attributes** use the same style: `tags_attributes[][name]=x`.
- **Building identifiers** come in two forms:
  - `:sp_number`: the strata plan string, e.g. `SP12345`. URL-encode it.
  - `:account_id`: the numeric building id, from `building_profile.id`.

### Errors
Error bodies take one of two forms:
- `{"message": "...", "status": "error"}`
- `{"error": "Oops! Page not found"}`

Typical status codes:
- `403`: your role lacks permission.
- `404`: not found, or not visible to you.
- `422`: a required parameter is missing.
- `500`: bad filter values, e.g. a wide `start_date`/`end_date` range.

---

## 4. API map and endpoint index

### Resource map
How the resources hang together. Shaded nodes carry downloadable files (see [§17](#17-where-files-live)).


### Crawl order to capture everything an owner can see
1. `GET /v1/users/me` and `GET /v1/building_profile` give the `sp_number` and `account_id` of each building.
2. Documents: page through `GET /v1/documents` **twice**, once with `is_invoice=false` and once with `is_invoice=true`. Then call `GET /v1/documents/{id}` for each one to get the detail fields, the variant links and the invoice attachments.
3. Jobs: page through `GET /v1/jobs`, then call `GET /v1/jobs/{id}` for each to get the quote and invoice attachments. Call `GET /v1/building_profile/{sp}/jobs` to get the job images.
4. Meetings: `…/{account_id}/meeting_registers`, then the agendas and attachments of each register.
5. Chat: `GET /v1/chat_rooms` and `…/archived_chat_rooms`, then `GET /v1/chat_rooms/{id}?messages=true&users=true&attachments=true` for each room.
6. The rest: `GET /v1/crystal_report_request_items`, `…/{sp}/images`, `GET /v1/notifications`, and the reference data (contacts, lots, categories, GL codes, contractors).

### Observed volumes
Counts for **one lot-owner account** on 2026-10-06. Other accounts will differ. The list shows which endpoints actually return data.

- **Documents** (`is_invoice=false`): 231, all with files.
- **Invoice documents** (`is_invoice=true`): 105, all with files. None had been captured by a documents-only sync.
- **Jobs:** 121, with 21 attachments between them. 20 of those attachments don't appear in any document list.
- **Meeting registers:** 45. Agendas and resolutions come back as HTML. No attachments seen.
- **Notifications:** 105. No files.
- **Chat rooms:** 1 active, 3 archived. They carry attachments and message files.
- **Generated financial reports:** 20, each with a `file_url`.
- **Building images:** 1.
- **Contacts, locations, site plans, organisations, categories:** small lists. No files.
- **Empty for this account:** `meetings`, `digital_noticeboards`, `building_inspection_sessions`, `tags`.
- **Returned `403`/`404` for this owner:** `documents/organisation_documents`, `bulk_attachment_downloads`, `meeting_records`, `strata_master_creditors`, `strata_master_data_logs`, `bpay_crns`, `invoice/duplicates`.

### Endpoint index
- [Documents](#5-documents): list, detail, search, group codes, edit, delete, favourite, priority, sharing
- [Invoices](#6-invoices-document-sub-resources): approval workflow on invoice documents
- [Jobs](#7-jobs-work-orders): list, detail
- [Building profile](#8-building-profile): buildings, detail, images, contacts, lots, jobs, rules, noticeboards
- [Meetings](#9-meetings): registers, agendas, attachments
- [Messages](#10-messages-chat-rooms): chat rooms, messages
- [Notifications](#11-notifications): notifications and settings
- [Users and contacts](#12-users-and-contacts): me, users, contacts, registration
- [Organisation and reference data](#13-organisation-and-reference-data): organisations, locations, categories, tags, contractors, GL codes
- [Reports](#14-reports): financial (Crystal) reports, bulk downloads
- [Digital noticeboards](#15-digital-noticeboards): screens, templates
- [Integrations and staff tools](#16-integrations-and-staff-tools): StrataMaster, inspections, invoice financing, analytics

---

## 5. Documents

### `GET /v1/documents` ✅
Paginated list of the documents visible to the user.

**Query parameters:**
- `page`, `per_page` (int): `per_page` works up to at least 100.
- `time_zone` (string): an IANA zone.
- `is_invoice` (bool): **defaults to `false`.** Invoices are only returned with `is_invoice=true`, so a complete listing needs both calls.
- `sp_number` (string): filter by building.
- `organisation_id` (int)
- `category` (string)
- `tags` (string)
- `is_levy_notice` (bool): the app sets this instead of `category` for levy-notice categories.
- `q` (string): keyword.
- `full_search` (string): full-text search, used when there are no other filters.
- `start_date`, `end_date` (date): a very wide range returned `500`.
- `order`, `sort` (string): sort direction and field.
- `favorites`, `my_tasks`, `priority_invoices`, `invoice_duplicate` (bool): filters.
- `only_deleted` (bool): deleted documents. Returned 0 for an owner.
- `scope` (`all`): staff only. No effect for an owner: `total_count` is unchanged.

A complete owner listing is the union of `is_invoice=false` and `is_invoice=true`. No other parameter combination returned more documents.

**Response:**
```
documents[]:
  id                          int
  filename                    string    original upload filename
  display_name                string    human title
  file_extension, file_type   string
  file_size                   string    "356 KB"
  category                    string    free text, e.g. "minute book", "Levy Notice"
  is_invoice                  bool
  sp_number                   string
  lot_number, lot_number_id   string|null, int|null
  notes                       string|null
  source                      string|null   e.g. "StrataMasterDms", "FileSmart", "Email", "Smata Job"
  created_at, updated_at      epoch
  added_date                  epoch|null
  popular, priority, is_deleted, exported_to_strata_master   bool
  owner_id, owner_name        int, string|null
  image_type                  string
  user {id, full_name}        uploader
  additional_users[] {id, full_name}
  tags[], currently_with, currently_with_user_details[], approvers
  attachment_approvals[], assigned_attachment_approvals[], my_attachment_approval
  account_documents[], invoice_approval_v2
  links {file_url, document_url, thumb_url, pdf_url}
  # invoices only:
  invoice {id, invoiced_price, invoice_number, payment_method, status, date, due_date,
           schedule_date, payment_date, payment_terms, is_duplicated, ready_for_approval,
           invoice_funding, invoice_line_items[] {id, description, gst, amount, gl_code,
           gl_code_prefix, group_code}}
  job {...}
  contractor {id, name, abn, abn_invalid, active_external_resource_organisation_ids[]}
meta: {total_count, page, per_page, total_pages, favorites[], my_tasks[], unseen_documents[]}
```

### `GET /v1/documents/organisation_documents` 🔒
Same parameters as `GET /v1/documents`, plus `scope=all`. The app uses it when the user has the `document.view_all` permission (see `users/me.document_permissions`).

### `GET /v1/documents/{id}` ✅
Document detail. It returns more than the list does. Fields beyond the list:

```
description                 string|null
deleted                     bool
history[] {name, created_at}            audit trail ("X has uploaded this Document", ...)
building {value, address, label, image_url}
unit_number, organisation_id, is_favorite, external_creditor_id, bpay_crn_id, use_custom_bpay_crn
admin_fund_balance, sinking_fund_balance, available_balance, investment_balance, funds_reserve
                            number   building fund balances; change over time
account_share_with_users {owners[], committee_members[]}
building_document_permissions[] {id, role, key, value, label}
organisation_category_settings[] {id, role, enabled, label}
attachment_approvals {reviewers[], approvers[], currently_with[], currently_with_user_details[],
                      my_attachment_approval}
additional_users[] {id, full_name, first_name, last_name, is_approver, role, image}
links {file_url, file_url_v2, original_file_url, other_documents_url, document_url, thumb_url, pdf_url}
# invoices: invoice {... + attachments[], invoice_line_items[] (with gl_code_full_name,
#   external_transaction_id), strata_master_creditor {name, bsb, account_no, ...},
#   job_trade, sequence, po_number, gst, ...}
#   contractor {..., compliance_items[], bsb, account_no, contact emails}
```

- Returns `404 {"message":"Attachment Not Found"}` for documents that were removed, or that you no longer have access to. On the test account, about 880 documents that an earlier sync had listed later disappeared from the list and returned this `404`. The API gives no way to tell a deletion apart from lost access.
- `links.file_url_v2` is a presigned URL that expires after 15 minutes. `links.file_url` is a stable, unauthenticated URL.

### `GET /v1/documents/search` ✅
Returns the facet values used to build the filter dropdowns. It does not return documents.

**Query parameters:**
- `q` / `full_search`: search text.
- All the filters from the document list.

**Response:**
```
sp_number[], type[], tag[], contractor[], invoice_number[]
# app also reads: amounts[], lot_number[], invoice_status[], documents[], invoices[], images[], user_name[]
```

### `GET /v1/documents/group_codes` ✅
- **Query parameters:** `sp_number` (the building).
- **Response:** `group_codes[] {id, name}`. These are accounting group codes.

### Document writes ✏️
- `POST /v1/documents`: upload. Multipart file plus metadata (category, `sp_number`, …).
- `PUT /v1/documents/{id}`: edit. `display_filename`, `category`, `filename`, `tags_attributes[][name]`, `additional_users_attributes[][id]` (sharing).
- `DELETE /v1/documents/{id}`: delete. Body `{note}`.
- `PUT /v1/documents/{id}/mark_as_seen`: mark as seen. No body. It clears the item from `meta.unseen_documents`.
- `PUT /v1/documents/{id}/update_favorite`: toggle favourite. No body.
- `PUT /v1/documents/{id}/update_priority`: set priority. Body `{priority, reason}`.
- `POST /v1/documents/{id}/remind_action_users`: remind approvers. `remind_user_ids[]`.

---

## 6. Invoices (document sub-resources)

An invoice is a document with `is_invoice: true`. Every route below is ✏️ (staff or approver workflow) unless marked otherwise.

- `GET /v1/documents/{id}/invoice/duplicates` 🔒: possible duplicate invoices. Returns `documents[]`.
- `PUT /v1/documents/{id}/invoice/approve`: approve. Body `{status_note}`.
- `PUT /v1/documents/{id}/invoice/approve_and_pass`: approve and pass to the next approver. Body `{note, next_approver_id, excluded_approver_ids[], pass_to_external_approvers}`.
- `PUT /v1/documents/{id}/invoice/override`: approve for payment (override). Body `{note}`.
- `PUT /v1/documents/{id}/invoice/re_export`: re-export to StrataMaster. No body.
- `PATCH /v1/documents/{id}/invoice/update_status`: change status. Body `{commit, status_note, notify_contractor, schedule_date}`. `commit` is one of `hold`, `unhold`, `cancel`, `reject`, `reopen`, `schedule_payment`, `mark_as_payment_required`, `mark_as_no_payment_required`.
- `PATCH /v1/documents/{id}/attachment_approvals/approve_and_pass`: approval v2. Body `{note, next_approver_id, excluded_approver_ids[], pass_to_external_approvers}`.
- `PATCH /v1/documents/{id}/attachment_approvals/assign`: assign an approver. Body `{assignee_id, note}`.
- `PATCH /v1/documents/{id}/attachment_approvals/transfer_assignment`: transfer. Body `{current_approver_id, new_approver_id, note}`.
- `PUT /v1/documents/{id}/assign_to_user`: assign. Body `{user_id, status_note}`.
- `PATCH /v1/job_trade_contractors/{id}/update_state`: reject a contractor invoice. Body `{commit: "reject_invoice", status_note, notify_contractor}`.

---

## 7. Jobs (work orders)

### `GET /v1/jobs` ✅
List of jobs: maintenance work orders, quote requests and similar.

**Query parameters:**
- `page`, `per_page`
- `q`: keyword.
- `sp_number`: building.
- `contractor_id`

**Response:**
```
jobs[]:
  id, job_reference_id (8-char code, e.g. "ABCD1234"), access_token (string; per-job link token — treat as secret)
  account_id, sp_number, location, issue_location, unit_number, unit_suite_no
  title, description, job_type, term, state, urgent, cancellable, not_strata_related
  creator_name, job_owner {id, email, full_name, role, phone_number, ...}
  managers[] {id, email, full_name, role}, managers_count
  job_trades[]  (see detail)
  job_trades_count, job_trades_inspections[], counter_time_outs[]
  recent_activity {job: {create, close_job, create_comment}}
  created_at, updated_at
meta: {total_count, page, per_page, total_pages, my_tasks[]}
```

### `GET /v1/jobs/{id}` ✅
Job detail. This route never appears in the web app's code, but it works. It is the only place where a job's **quotes and invoices, with their attachments**, are exposed.

```
(all list fields) +
history[] {id, key, description, author_id, author, created_at}    full audit trail
creator {...}, organisation {id, name, country, state, time_zone, logo, phone_number}
location_id, location_lat_lng, sp_number_with_prefix, noticeboard_id
account_admin_fund_balance, account_sinking_fund_balance
preferred_dates, preferred_start_time, preferred_end_time, require_work_photos, ...
requirements[], contacts[]
job_trades[]:
  id, status, state, description, job_type, trade_type, due_date, time_frame,
  contact_name, mobile_number, minimum_number_of_quotes, chargeable_quote, ...
  job_trade_contractors[] {id, state, contractor_id, contractor_name, work_scheduled_date,
                           work_scheduled_time, estimated_duration, rating, reviews_count,
                           contractor_image, profile_image, inspections{}, time_outs{}, history[]}
  assigned_team_members[] {id, full_name, mobile_number, profile_image}
  voting_details {vote_count, total_user_count, casting_count, is_majority_reached}
  quotes[]   {id, state, quote_price, description, contractor_name, duration_display,
              estimated_time_frame_display, quote_avatar_url, progress_payments[],
              attachments[] (FILE)}
  invoices[] {..., attachments[] (FILE)}
  last_invoice, recent_invoice   (same shape, may duplicate invoices[])
  comments[], variance, disruption_notice, job_trade_schedules[]
```

Each attachment object has this shape:
```
{id, file_url, file_name, file_extension, file_type, file_size, type, description,
 creator_name, popular, image_type}
```

Most job attachments are **not** returned by `/v1/documents`.

---

## 8. Building profile

### `GET /v1/building_profile` ✅
The buildings the user belongs to.

```
buildings[]: {id (account_id), site_plan_id (SP number), raw_site_plan_id, total_lots, state,
              association_type_name, account_active, organisation {id, name},
              strata_manager {id, full_name, image}, support_manager, locations[] {id, location_name},
              main_large_image_url, opened_invoices_count, maintenance_management_activated,
              building_manager_exists, digital_noticeboard_feature, amount_is_over{...},
              external_approvers_needed, building_screen_installation}
meta
```

### `GET /v1/building_profile/{sp_number}` ✅
Building detail.

```
id, organisation_id, site_plan_id, raw_site_plan_id, body_corporate_name, total_lots,
number_of_voters, votes, state, financial_year_end (epoch), action_required_invoices_count
strata_manager {id, full_name, email, phone_number, organisation, rating[], ...}
building_manager {...}
building_rules {one_off_invoices {stamp_required, amount_is_over_internal,
                amount_is_over_external, external_approvers_needed, approvers[]},
                recurring_invoices[], histories[]}
contacts[] {id, full_name, lot_number}
upcoming_work[] {id, job_reference_id, access_token, title, description, status, job_statuses[],
                 job_trade_contractor_statuses[], building_contacts[], job_owner{...}, ...}
locations[] {id, account_id, location_name, building_name, label, lat_lng, site_plan_id,
             authorised_limit, work_order_cost_limit, default_location, uuid, ...}
noticeboards[], building_team_users[], custom_building_services[]
theme {primary_color, secondary_color, logo}
can_* permission flags (create_work_request, view_recent_works, generate_financial_report,
  view_building_directory, view_upcoming_works, view_star_rating, view_building_rule,
  view_levy_notices, message, view_digital_noticeboards, view_building_funds,
  view_building_insurances)
```

### `GET /v1/building_profile/site_plans` ✅
- **Query parameters:** `site_plan_ids[]` (optional filter).
- **Response:** a bare array:
  ```
  [{id, name, label, value, state, association_type_name, financial_year_end,
    available_balance, funds_reserve, can_message, main_image_url, disabled,
    locations[] {id, location_name, state},
    permissions {can_access_messages, can_create_work_request, can_generate_financial_report,
                 can_view_building_directory, can_view_building_funds, can_view_levy_notices,
                 can_view_building_rule, can_view_recent_works, can_view_manager_star_rating,
                 can_view_upcoming_works, can_view_insurance_policies, can_view_digital_noticeboards}}]
  ```

### Building sub-resources (`/v1/building_profile/{sp_number}/...`)

#### `GET .../images` ✅
- **Response:** `images[] {id, image_url (FILE), name, description, is_main_image}`

#### `GET .../building_contacts` ✅
- **Response:** a bare array `[{id, email, full_name, first_name, last_name, role, contact_roles[], state, is_displayed_in_contact_list, image}]`

#### `GET .../lot_numbers` ✅
- **Response:** `lot_numbers[] {id, name, label, value, external_lot_id}`

#### `GET .../jobs` ✅
- **Params:** `pagination=true`, `page`, `per_page`, `keyword`, `recent_job_status`, `upcoming_work=true`
- **Response:** `jobs[]` (compact jobs, **plus `images[]` (FILE)**), `meta`

#### `GET .../noticeboards` ✅
- **Response:** `noticeboards[]`, `meta`

#### `GET .../building_recurring_rules` ✅
- **Params:** `q`
- **Response:** `recurring_rules[]`

#### `GET .../building_recurring_rules/histories` ✅
- **Response:** `recurring_rule_histories[]`

#### `GET .../invoice_rules/histories` ✅
- **Response:** `invoice_rule_histories[] {name, created_at}`, `meta`

#### `GET .../bpay_crns` 🔒
- **Params:** `external_resource_id`
- **Response:** `bpay_crns[]`

#### Writes ✏️
- `PUT /v1/building_profile/{sp_number}`: invoice-rule settings (`one_off_stamp_required`, `one_off_invoices_internal`, `one_off_invoices_external`, `external_approvers_needed`, …).
- `PUT .../rate`: rates the manager. Query `user_id`, `vote`, `dimension`.
- `POST .../add_image`: multipart.
- `PUT .../set_main_image`: query `profile_image_id`.
- `DELETE .../remove_image`: query `profile_image_id`.
- `POST .../create_noticeboard`, `PUT .../update_noticeboard`, `DELETE .../delete_noticeboard`: in-app noticeboard posts. Multipart `title`, `text`, `cover_image`, `notify_*` flags; `noticeboard_id`.
- `POST`/`PUT`/`DELETE .../building_recurring_rules[/{id}]`: rule fields.

### Building sub-resources keyed by `account_id`

#### `GET /v1/building_profile/{account_id}/building_document_categories` ✅
- **Params:** `page`, `per_page`, `by_keyword`
- **Response:** `building_document_categories[]`, `meta`
- **Writes ✏️:** `POST`, `PUT /{id}`, `DELETE /{id}`, `GET /histories`

#### `GET /v1/building_profile/{account_id}/meeting_registers` ✅
See [Meetings](#9-meetings).

---

## 9. Meetings

### `GET /v1/building_profile/{account_id}/meeting_registers` ✅
The register of general and committee meetings.

- **Query parameters:** `page`, `per_page` (default 50).
- **Response:**
  ```
  meeting_registers[] {id, meeting_date (epoch), meeting_venue, meeting_type (e.g. "Annual General
                       Meeting"), state, internal, can_vote, voted, meeting_id}
  meta
  ```

### `GET /v1/building_profile/{account_id}/meeting_registers/{id}` ✅
Fields beyond the list:
```
lot_numbers[] {id, name}
my_voting_lot_numbers[] {id, name}
building {id, sp_number, name}
```

### `GET /v1/meeting_registers/{meeting_register_id}/meeting_agendas` ✅
```
meeting_agendas[] {id, agenda_number, description, formatted_text (HTML), resolution_text (HTML),
                   meeting_records[]}
meta
```

Writes: `POST` and `PUT /{id}` ✏️.

### `GET /v1/meeting_register_attachments` ✅
- **Query parameters:** `meeting_register_id` (required). Without it the call returns `404`.
- **Response:** `meeting_register_attachments[]` (FILE), `meta`.
- **Writes:**
  - `POST` ✏️: multipart `title`, `attachment`, `meeting_register_id`
  - `DELETE /{id}` ✏️

### `GET /v1/meetings` ✅
- **Query parameters:** `account_id` (the building), `internal` (bool).
- **Response:** `meetings[]`, `meta`. These are internal or StrataMaster meetings. The list was empty for the test account.

### `GET /v1/meeting_records` 🔒
Returned `404`.

---

## 10. Messages (chat rooms)

### `GET /v1/chat_rooms` ✅
Active conversations.

**Query parameters:**
- `page`, `per_page`
- `users`, `attachments`, `last_message`, `location`, `unread_count` (bools): include the related data.
- `show_all` (bool)
- `q`: search.

**Response:**
```
chat_rooms[] {id, name, created_at, is_deleted, unread_count,
              account {site_plan_id, raw_site_plan_id}, location {id, location_name},
              last_message {id, body, is_deleted, created_at, sender {id, first_name, last_name,
                            email, role}},
              users[] {id, email, full_name, first_name, last_name, role, organisation_id},
              attachments[]}
meta {..., unread_chat_rooms_ids[]}
```

### `GET /v1/chat_rooms/archived_chat_rooms` ✅
Same parameters and response as the active list, for archived conversations. Here `attachments[]` holds document objects:
```
{id, filename, display_name, file_extension, file_size, category, url (FILE), created_at,
 locations[], links {file_url, document_url, thumb_url, pdf_url}}
```

### `GET /v1/chat_rooms/{id}` ✅
A conversation with its messages. Pass `messages=true`, and optionally `users`, `attachments`, `location`, `account` and `unread_count`.

```
id, name, created_at, is_deleted, unread_count, account{}, location{}, users[], attachments[]
messages[] {id, body (HTML), created_at, updated_at, is_read, is_deleted, type,
            sender {id, first_name, last_name, email, role},
            message_files[] {id, name, file_name, file_extension, file_size, type, url (FILE),
                             file {url, carousel{url}, xlarge{url}, large{url}, medium{url},
                                   small{url}, thumb{url}, pdf{url}}}}
histories[] {id, type, name, created_at}
```

- `GET /v1/chat_rooms/search_without_tags` 🔍 is used by the search page.
- `GET /v1/chat_rooms/{id}/messages` returns `422 Missing q parameter`. It is a search endpoint, not the way to list messages.

### Message writes ✏️
- `POST /v1/chat_rooms`: body `{name, location_id, chat_room_users_attributes[] {user_id}, chat_room_attachments_attributes[] {attachment_id}, messages_attributes[] {body}}`.
- `PUT /v1/chat_rooms/{id}`: body `{name, user_ids[], attachment_ids[]}`.
- `POST /v1/chat_rooms/{id}/messages`: body `{body, message_files_attributes[]}`.
- `PATCH /v1/chat_rooms/{id}/messages/{message_id}`: query `body`.
- `DELETE /v1/chat_rooms/{id}/messages/{message_id}`
- `PATCH /v1/chat_rooms/{id}/read_messages`: marks the conversation read.
- `PUT /v1/chat_rooms/{id}/restore`: un-archives.

---

## 11. Notifications

### `GET /v1/notifications` ✅
- **Params:** `page`, `per_page`, `type` (`read` or `unread`)
- **Response:**
  ```
  notifications[] {id, key (e.g. "job.internal_job_created"), category, title (HTML), body, info{},
                   url, from, is_read, created_at, sp_number, location_address,
                   notifiable {id, category, chat_room_id, job_id, site_plan_id, noteable_type, noteable_id},
                   job {id, title}}
  meta
  ```

Notification `url` values can contain job `access_token`s. Treat them as secrets.

### `GET /v1/notifications/has_unread` ✅
- **Response:** a bare boolean.

### `GET /v1/notification_setting` ✅
- **Response:** `{snoozed, snoozed_until, breakdown[] {id, key, name, description, level, email, sms, app, email_frequency, *_available, notification_option_id}}`

### `GET /v1/notification_setting/options` ✅
- **Response:** `{snooze_periods[] {value, label}, email_frequencies[] {value, label}}`

### Notification writes ✏️
- `PUT /v1/notifications/{id}`: body `{is_read}`.
- `PUT /v1/notifications/mark_all_read`
- `PUT /v1/notification_setting`: the same structure as the `GET`.

---

## 12. Users and contacts

### `GET /v1/users/me` ✅
The current user.

```
id, email, full_name, first_name, last_name, initials, role, phone_number, mailing_address,
organisation_id, state, image, online, last_seen_at, namespace
default_building_profile_id, country, root_url, current_enabled_subdomain, formatted_timezone_offset
subroles[] {contact_id, role, organisation_id, sp_number, unit_number, lot_number_id,
            lot_number_name, vote}
document_permissions {"document.view_all", "document.edit", "document.delete", "invoice.edit",
                      "invoice.delete", "invoice.override", "invoice.view_all",
                      "contact.invite_building_contacts"}
feature_flags {...}  (~35 booleans: strata_master_integration, two_fa, digital_noticeboard, ai, ...)
theme {...}, two_fa_enabled, should_use_notes_filename, new_ui_enabled,
active_unread_messages_count, archived_unread_messages_count, action_required_invoices_count,
is_displayed_in_contact_list, is_receiving_messages, is_job_notifications, email_digest_setting,
compare_connect {url, lead{...}}, pendo {...}, global_banners[], organisation_banners[], ...
```

### `GET /v1/users` ✅
- **Params:** `organisation_id`, `user_role`, `user_state` (or `user_state[]`), `building_managers_by_sp`, `managers`, `old=1`, `page`, `per_page`
- **Response:** `users[] {id, email, full_name, first_name, last_name, role, phone_number, organisation_id, state, online, last_seen_at, ...}`

### `GET /v1/users/contacts` ✅
The building directory.
- **Params:** `sp_number`, `role`
- **Response:** `contacts[] {id, email, full_name, first_name, last_name, role, mobile_number, user_id, lot_number, unit_number, state, is_displayed_in_contact_list, is_receiving_messages}`, `meta {strata_manager{...}, building_manager{...}}`

### `GET /v1/location_contacts` ✅
The current user's own memberships.
- **Response:** a bare array `[{id, lot_number, unit_number, contact_role, location_name, mailing_address, site_plan_id, raw_site_plan_id, active, account_state, quote_approval, documents_by_post, variance_approval, emergency_contact, default_job_contact}]`

### `GET`/`POST /v1/invitation_tokens` 🔒

### User writes ✏️
- `PUT /v1/users/{id}`: body `{email, first_name, last_name, mobile_number, mailing_address}` or `{two_fa_enabled}`.
- `PUT /v1/users/{id}/update_password`: password fields.
- `PUT /v1/users/update_profile_image`: multipart.
- `PUT /v1/users/update_settings`: body `{is_displayed_in_contact_list, is_job_notifications}`.
- `POST /v1/strata_member/registrations/{token}/step_1`: owner self-registration. Body `{password, password_confirmation}`.
- `POST /v1/strata_member/registrations/{token}/complete`: body `{password, password_confirmation, first_name, last_name, mobile_number, email}`.

---

## 13. Organisation and reference data

### `GET /v1/organisations` ✅
- **Params:** `use_strata_master`
- **Response:** `organisations[] {id, name}`, `meta`

### `GET /v1/organisation_users` ✅
- **Response:** `organisation_users[] {id, organisation_id, name, role, logo_url, colour_web_general, mobile_access, new_ui_by_default, ai, smart_search}`
- `PUT /v1/organisation_users/{id}` ✏️ switches organisation.

### `GET /v1/locations` ✅
- **Params:** `q`, `page`, `per_page`, `only_default_locations`, `by_account_state`
- **Response:** a bare array of location objects (see the building detail).

### `GET /v1/categories` ✅
The document categories.
- **Params:** `q`
- **Response:** `categories[] {id, value, label}`

### `GET /v1/tags` ✅
- **Params:** `q`, `page`, `per_page`
- **Response:** `tags[] {name, label}`

### `GET /v1/contractors` ✅
- **Params:** `organisation_id`, `search`, `q`, `page`, `per_page`, `web`
- **Response:** `contractors[] {id, name, abn, bsb, account_no, payments_email, gst_registered, large_profile_image, compliance_items[] {name, state, type}, compliance_valid, compliance_exempt, strata_master_creditors[]}`, `meta`
- `POST /v1/contractors/{id}/compliance_reminder` ✏️

### `GET /v1/gl_codes` ✅
The general-ledger chart of accounts.
- **Params:** `organisation_id`, `sp_number`, `fund_id`
- **Response:** `gl_codes[] {id, name, full_name, account_number, is_admin, fund_id, current_account {id, sp_number, raw_site_plan_id}}`

### `GET /v1/gl_codes/recently_used` 🔍
- **Params:** `sp_number`, `creditor_id`
- **Response:** `gl_codes[]`

### `GET /v1/april_pay_industries` ✅
- **Response:** a bare array (empty).

### `POST /v1/help` ✏️
Support single sign-on. Returns `{zendesk_redirect_url}`.

---

## 14. Reports

### `GET /v1/crystal_reports` ✅
The financial report types that can be generated for a building.

- **Query parameters:** `account_id` (the building).
- **Response:**
  ```
  crystal_reports[] {id, key, title, description, categories[], lotable,
                     trigger_association_filter, value, label}
  ```

The call works, but the list can be empty for an owner. It was empty in the first survey. Reports that were generated earlier are still listed under `crystal_report_request_items`, below.

### `GET /v1/crystal_report_request_items` ✅
Reports the user has generated before. **Each item has a `file_url` (FILE).**

```
crystal_report_request_items[] {id, status ("initiated"|"scheduled"|"in-progress"|"completed"|"error"),
                                file_url, request_period, created_at, site_plan_ids[],
                                report_titles[], lots[]}
meta
```

- `GET /v1/crystal_report_request_items/{id}` 🔍 is polled by the app until the report's `status` is `completed`.
- `POST /v1/crystal_report_request_items` ✏️ requests a report. The body holds the report ids, the period, `account_ids` and `lot_number_ids`.

### `GET /v1/bulk_attachment_downloads` 🔒
Bulk "download all" jobs.

- **Query parameters:** `page`, `per_page`.
- **Response:** `bulk_attachment_downloads[]`, `meta.total_count`.
- **Writes:** `POST` and `DELETE /{id}` ✏️.

---

## 15. Digital noticeboards

### `GET /v1/digital_noticeboards` ✅
- **Params:** various filters.
- **Response:** a bare array.
- **Writes ✏️:** `POST /v1/digital_noticeboards`, `PUT /v1/digital_noticeboards/{id}`, `DELETE .../{id}`. They take a template id, a screen id, content and `notify_*` flags.

### `GET /v1/digital_noticeboard_templates` ✅
- **Response:** `digital_noticeboard_templates[] {digital_noticeboard_template_id, value, label, remote_name, logo, heading, main_copy, image, extra_copy, qr}`

### `GET /v1/digital_noticeboard_screens` ✅
- **Params:** `by_account`, `q`
- **Response:** a bare array.

---

## 16. Integrations and staff tools

These are mostly for strata-manager roles. Listed for completeness.

- `GET /v1/strata_master_creditors` 🔒: params `organisation_id`, `contractor_id`, `q`, `search`. Returns `creditors[]`.
- `GET /v1/strata_master_data_logs` 🔒: params `organisation_id`, `resource_name`, `page`, `per_page`.
- `GET /v1/strata_master_data_logs/available_resources` ✅: returns `available_resources[] {name, pretty_name, use_secondary_id}`.
- `GET /v1/strata_master_data_logs/preview_missing_items` 🔍: params `organisation_id`, `resource_name`.
- `GET /v1/strata_master_data_logs/{external_id}/data_log_details` 🔍: params `organisation_id`, `resource_name`, `page`, `per_page`, `secondary_external_id`.
- `POST /v1/strata_master_data_logs/import_missing_items`, `PUT .../{id}/collect_from_strata_master`, `PUT .../{id}/sync_with_strata_master` ✏️
- `/external_apps/strata_master/...` (`organisations`, `import_wizard`, `third_party_import_logs`) 🔍/✏️: the StrataMaster import wizard. These paths sit outside `/v1`.
- `/external_apps/good_data/dashboards`: returned `422`. Analytics dashboards (GoodData).
- `/external_apps/compare_connect/leads` ✏️: insurance lead referral.
- `GET /v1/building_inspection_sessions`, `GET .../current_session` ✅: the building-inspector role. Writes: `PUT .../{id}/stop`, `PUT .../{id}/extend_duration` ✏️.
- `/v1/invoice_financing_contractors/:id`, `/v1/invoices/:invoice_id/invoice_fundings[/new|/{id}]` 🔍/✏️: invoice financing.

---

## 17. Where files live

Every downloadable file is exposed as a URL inside a JSON response. These URLs are on `s3.smata.com` and **need no authentication**, so anyone who has the URL can download the file.

- **Documents:**
  - `documents[].links.file_url`: the main file.
  - `links.original_file_url`, `links.pdf_url`: variants. Detail only, and often `null`.
  - `links.thumb_url`: thumbnail.
  - `links.file_url_v2`: presigned. It expires after 900 seconds.
- **Invoice documents:** `invoice.attachments[].file_url`, the supporting attachments. Detail only.
- **Job quotes:** `/v1/jobs/{id}` → `job_trades[].quotes[].attachments[].file_url`.
- **Job invoices:** `/v1/jobs/{id}` → `job_trades[].invoices[].attachments[].file_url`. Also under `last_invoice` and `recent_invoice`.
- **Job images:** `/v1/building_profile/{sp}/jobs` → `jobs[].images[].file_url`.
- **Meeting attachments:** `/v1/meeting_register_attachments?meeting_register_id=`.
- **Chat attachments:** `chat_rooms[].attachments[].url` or `links.file_url`.
- **Chat message files:** `messages[].message_files[].url`, with several sizes under `file.*`.
- **Generated reports:** `crystal_report_request_items[].file_url`.
- **Building photos:** `/v1/building_profile/{sp}/images` → `images[].image_url`.

A document's file URL follows this pattern:
```
https://s3.smata.com/uploads/file_attachment/attachment/<document_id>/<filename>
```

---

## 18. Notes for API clients

- **Only issue `GET` requests** if you are archiving. Some reads have side effects in the web app, and the app makes them through separate write calls: `mark_as_seen`, `read_messages`, `mark_all_read`. An archiver must not call those.
- **Throttle your requests.** The web app has a `/429` page, so rate limiting exists. The limits are unknown.
- **Documents disappear.** `GET /v1/documents/{id}` starts returning `404` for items that were listed before, so keep your own copy.
- **Job `access_token`s and the S3 file URLs** grant access without logging in. Treat them as secrets.

### Archiver checklist
- [ ] Send `GET` only. Never call `PUT .../mark_as_seen`, `PATCH .../read_messages`, `PUT /v1/notifications/{id}` or `PUT /v1/notifications/mark_all_read`. They change what the account owner and the manager see.
- [ ] List documents with both `is_invoice=false` and `is_invoice=true`.
- [ ] Fetch the detail of every document and every job. The list responses leave out history, variant files, invoice attachments, and job quote and invoice attachments.
- [ ] Download the `links.original_file_url` and `links.pdf_url` variants whenever they are non-null.
- [ ] Keep volatile fields under a separate key so that stored metadata stays stable from run to run. These are `links.file_url_v2` (expires after 900 seconds) and the five building fund balances on document detail.
- [ ] Record `first_seen` and `last_seen` for each item. Never delete a local copy because an item stopped being listed.
- [ ] Match files to items by id, not by filename. `category` and `display_name` can be edited after upload.
- [ ] Throttle requests, and back off on `429`.
- [ ] Never publish an archive folder or its metadata. The S3 URLs and job `access_token`s inside them grant access without a login.
