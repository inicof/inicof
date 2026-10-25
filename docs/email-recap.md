# Personalized daily money recap

Status: an authenticated local web prototype implements the dashboard, preferences,
HTML email preview, ledger, and correction workflow. Automatic sending, scheduled
jobs, bank-specific imports, and production hosting remain unimplemented.

This document describes public application behavior only. Selected categories,
reporting preferences, recipient details, transactions, and budget values must
remain in private household settings and storage.

## Requested email layout

1. **Three featured categories at the top.** The first section visible on opening
   the email contains the three categories selected by the household, in their
   chosen order. Each shows its name, the amount remaining, and spending for the
   selected reporting period. Keep this section compact and readable on a phone.
   The actual categories and reporting-period controls will be decided later.
2. **The complete budget further down.** Include every configured budget line,
   including categories outside the featured three, with its allocation,
   spending, and remaining balance where applicable. The full budget must be
   readable by scrolling the email, without requiring a website visit.
3. **Yesterday's transactions.** For each transaction show the date, merchant or
   description as supplied by the SMS/notification, amount and currency, assigned
   category or review status, and a clearly labeled **Change category** link.
   An optional cleaned merchant name must not replace the original description.
   Avoid showing unrelated account balances or the entire raw bank message.

## Reporting behavior

- Clearly label the period for each spending figure, such as week to date or
  month to date. Do not label a monthly remaining balance as a weekly allowance.
- Remaining budget must use the household's allocation and rollover rules, not
  simply subtract this week's spending from the monthly allocation.
- Show uncapped categories as uncapped, with spending but no invented remaining
  allowance. Preserve the distinction between savings and spending categories.
- Keep currency totals separate. Never silently convert or combine currencies.
- Interpret yesterday as the previous calendar day in the configured household
  reporting timezone, with explicit date boundaries. Define the week boundary
  before implementing weekly reporting.
- Show transactions needing review, possible duplicates, and excluded transfers
  with clear statuses. Do not count unconfirmed entries in confirmed totals.
- Show the report's generation time and any known incomplete-data warning. An
  empty activity period should say that no transactions were recorded, rather
  than claim that no spending occurred.

## Correcting a category from the email

1. Clicking **Change category** opens the website on that specific transaction.
   If login is needed, return to the transaction after sign-in.
2. Show the original merchant description, amount, currency, and current category
   so the person can verify the item before choosing a replacement category.
3. Saving changes that transaction's category and confirms the result. Recalculate
   affected category totals, remaining balances, and subsequent rollover balances
   where applicable. Preserve the transaction amount, currency, and source record.
4. Preserve manual categorization on later imports and automatic processing;
   keep an audit record of the previous category, new category, editor, and time.
5. Default to changing only this transaction. Remembering a merchant rule for
   future transactions may be offered separately and must be an explicit choice.

The website must check household access for every read and write. Use an opaque
transaction identifier in links; do not put raw SMS, amounts, or credentials in
URLs. Opening a link must never change data, including when an email client scans
or previews it. The save action requires an authenticated, authorized request
with protection against cross-site request forgery.

An already-sent email is a snapshot and cannot be rewritten. Corrections appear
in the live website and future recaps. Opening an old email link must show the
current transaction state; missing or inaccessible records need a safe message.

## Choices intentionally left open

- Which three categories to feature, and in what order.
- Weekly, monthly, or another spending period; whether to show more than one.
- The start of the reporting week and whether recipients have individual views.
- Detailed email styling, final section ordering below the summary, and the
  optional interface for remembering future merchant rules.

## Acceptance examples for implementation

Use fictional transactions only when building and testing these behaviors.

- The chosen three categories appear first, and every other budget line remains
  available further down the same email.
- A fictional supermarket transaction assigned to Transport can be changed to
  Groceries through its own email link. The amount is moved between category
  totals exactly once, with no duplicate transaction or currency conversion.
- Importing that same source transaction again preserves the manual correction.
- A recipient who is not signed in cannot read or change transaction data; a
  signed-in person outside the household cannot access it either.
- Email link scanning causes no category changes. Opening an old recap after a
  correction shows the latest category on the website.
