# Notification parsing and capture

The primary capture path is now a daily screenshot uploaded by a signed-in user,
processed with local OCR and explicit transaction review. Manually pasting a
supported purchase message is also available. See the
[screenshot architecture](screenshot-architecture.md).

This is separate from automatic capture on a phone. No Android companion app,
notification receiver API, or iPhone push integration exists. Android capture is
an optional future path, not a prerequisite for screenshot uploads.

## Currently supported

- One complete English ADCB credit-card purchase body, with an optional
  `ADCBAlert` heading. Whitespace wrapping and grouped decimal amounts are handled.
- The purchase currency and amount, original merchant description, last four
  card digits, and transaction timestamp. Available credit is intentionally
  discarded; it is neither a purchase amount nor income.
- Source timestamps are provisionally interpreted as UAE time (`Asia/Dubai`),
  then converted into the household timezone for daily and budget reporting.
  The bank timestamp and offset remain available on the transaction review page.
  Verify this assumption against the bank's records before relying on daily totals.
- Saved private merchant rules may suggest a category. Every new notification
  import still requires confirmation before entering budget totals.
- The parser rejects incomplete, stacked, unsupported, and security-code-only
  messages instead of guessing. Refunds, declined purchases, transfers, and card
  repayments need their own verified formats before automatic parsing is added.

The text-paste form accepts text; Daily screenshots accepts images. Neither
verifies that the submitted content originated from the bank. Confirmation and
statement reconciliation remain necessary.

## Android proof of concept still required

On a device the user controls, verify that the bank allows registration and sends
the expected transaction alerts to that device. Do not assume an iPhone screenshot
proves that Android will expose the same content or that both phones can stay linked.

An Android listener would require explicit notification-access permission. Test
that the OS and bank expose complete transaction text rather than a redacted
summary. Filter to the verified bank package and recognized transaction formats;
do not collect unrelated notifications or security codes. Notification group
summaries, replacement events, offline retries, and background restrictions need
testing before relying on delivery.

Any future receiver must authenticate the device, bind it to a household, and
use encrypted transport. A capture event ID is not a bank transaction ID. Repeated
delivery of one event must be idempotent, while separate purchases at the same
merchant and statement-versus-notification matches need careful reconciliation.
Statements remain the completeness check; missing alerts cannot imply no spending.

## Repository privacy

All parser and workflow tests use fictional merchants, identifiers, amounts,
timestamps, and credit limits. Never copy real notification screenshots, bodies,
or household categorization choices into this public repository.
