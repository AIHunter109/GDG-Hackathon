# Shipping verification usability check

Use the running dashboard with an operations reviewer who has not seen the implementation. Give the reviewer the task, then observe without explaining the interface. Record the date, tester role, completion time, errors, and comments. Do not enter confidential shipment data in a public demo.

| Task | Starting point | Success criterion |
| --- | --- | --- |
| Find a case needing attention | Dashboard | Uses a summary card or quick filter to open a mismatch or unresolved review case |
| Understand a mismatch | Case Detail | Selects the highlighted field and reads both source values |
| Inspect evidence | Case Detail | Expands attachments, opens the SI or draft BL, and finds the relevant line |
| Correct an uncertain value | Case Detail | Selects the right document and field, saves a correction, sees the updated result |
| Retry processing | Processing failed case | Uses Retry, sees Processing, and understands the new result |
| Finish a review | Dashboard and Case Detail | Resolves the case and sees it leave the Human Review filter |

## Observation record

| Tester role / date | Task | Completed? | Time | Wrong turns / confusion | Exact feedback | Change to make |
| --- | --- | --- | --- | --- | --- | --- |
| _To be filled during a user session_ | | | | | | |

## Local checks completed

- Automated HTTP workflow checks cover inbox loading, attachment text preview, reviewer correction, and confirming an extracted value.
- Pipeline checks cover category classification, aliases, normalization, document roles, consistency, pairing, scanned PDF fallback with a mocked AI response, and technical failure handling.
- The bundled 520-email submission is evaluated with the included local scorer. These checks do not replace a human usability session or a live Gemini/cloud deployment check.
